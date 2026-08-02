Found a concrete unsafe-arithmetic analog in the sharded-execution total-supply aggregation path, which computes a value that is written directly into the committed `WriteSet` for the `TOTAL_SUPPLY_STATE_KEY`.

### Title
Unchecked `u128` subtraction in `DeltaU128::add_delta` / `ops::Add` can panic or wrap, corrupting the committed total-supply write-set value during sharded block execution - (File: aptos-move/aptos-vm/src/sharded_block_executor/sharded_aggregator_service.rs)

### Summary
`aggregate_and_update_total_supply` reconciles the aggregator-based total-supply value across shards/rounds by combining per-shard/per-round deltas and applying them back onto each transaction's materialized `total_supply` write op. This mirrors the PoolTogether bug's structural pattern: an unguarded numeric operation on a value that becomes part of committed state, with no explicit safe/checked-arithmetic wrapper.

### Finding Description
`DeltaU128` is a manual sign-and-magnitude wrapper around `u128` used to represent "the intermediate computation can be negative" as noted in the code's own comment: [1](#0-0) 

The core arithmetic is: [2](#0-1) 

`add_delta` performs `other - self.delta` in the negative branch with a plain `-` operator on `u128`, and `ops::Add` performs `pos - neg` similarly: [3](#0-2) 

In Rust, `u128` subtraction is a checked/panicking operation only in debug builds; in release builds (the default for production nodes, `overflow-checks = false` unless explicitly enabled) it silently **wraps** on underflow. This is functionally identical to the PoolTogether bug's "silent overflow" scenario: if the invariant assumed by the surrounding logic (`other >= self.delta` in `add_delta`, `pos >= neg` already guarded in `Add` but not in `add_delta`) does not hold for some interleaving of shard/round deltas, the result silently wraps to an enormous, incorrect `u128` value instead of erroring out.

That corrupted value is then written directly into the ledger via: [4](#0-3) 
which calls into `TransactionOutput::update_total_supply` → `WriteSet::update_total_supply`, replacing the `TOTAL_SUPPLY_STATE_KEY` write op with the (potentially wrapped) value: [5](#0-4) 

Unlike the rest of the aggregator subsystem (`SignedU128`/`BoundedMath`/`DeltaWithMax`), which routes all signed deltas through `BoundedMath` with explicit `Overflow`/`Underflow` error returns (see `bounded_math.rs` and `delta_change_set.rs`), `DeltaU128` in the sharded-executor path bypasses that machinery entirely and uses raw `u128` arithmetic with only a partial guard (`if pos >= neg`) in one of the two call sites (`Add`) but none in `add_delta`'s negative branch beyond the (unverified) assumption that `other >= self.delta`.

### Impact Explanation
The `total_supply` value is a first-class piece of consensus-committed on-chain state (the `TOTAL_SUPPLY_STATE_KEY` write op), and it is hashed into the transaction's write-set hash which becomes part of the `TransactionInfo`/state-commitment. If `add_delta` wraps, every transaction's `total_supply` in that block gets corrupted to an arbitrary near-`u128::MAX` (or otherwise wrong) figure, which is committed as authoritative ledger state. This is a state-commitment integrity break: the value differs from the correct VM/aggregator result and is durably persisted, matching the "Committed state that differs from the correct VM result" impact bucket. Additionally, if built with overflow-checks enabled (some validator binaries build with panics-on-overflow), this becomes a deterministic-but-only-on-this-code-path panic (safety abort) — a liveness/hard-fork-adjacent issue for the sharded execution feature.

### Likelihood Explanation
This is gated to the (currently experimental/optional) sharded block executor path (`aggregate_and_update_total_supply` is only invoked when remote/local sharding is enabled), so it is not on the default single-node execution path. I could not fully verify all call sites/feature-flags gating this function, nor could I construct or verify a concrete sequence of shard/round deltas that provably triggers `other < self.delta` in `add_delta` (the surrounding comment implies the authors already know intermediate values "can be negative," suggesting the invariant is fragile by design, but I did not find an explicit correctness proof or test covering adversarial delta sequences). This uncertainty means I cannot state with certainty this is exploitable under normal validator configuration without further live testing.

### Recommendation
Replace the raw `u128` arithmetic in `DeltaU128::add_delta` and `ops::Add` with checked operations (`checked_sub`/`checked_add`) that return `Result`/`Option` and propagate an explicit error (matching the pattern already used by `BoundedMath` elsewhere in the aggregator crate), rather than allowing silent wraparound or an untested panic. Ideally, unify this ad hoc sign-magnitude type with `aptos_aggregator::bounded_math::SignedU128`, which already implements checked delta arithmetic with `Overflow`/`Underflow` errors.

### Proof of Concept
Not independently verified end-to-end (would require constructing a full sharded-execution run with crafted shard/round total-supply values such that `other < self.delta` in `add_delta`, which I could not exercise without running the code). The structural defect — unchecked `u128` subtraction on a value that flows straight into a committed write set — is directly evidenced by the cited code; a concrete triggering delta sequence remains unconfirmed.

### Citations

**File:** aptos-move/aptos-vm/src/sharded_block_executor/sharded_aggregator_service.rs (L36-57)
```rust
impl DeltaU128 {
    pub fn get_delta(minuend: u128, subtrahend: u128) -> Self {
        if minuend >= subtrahend {
            Self {
                delta: minuend - subtrahend,
                is_positive: true,
            }
        } else {
            Self {
                delta: subtrahend - minuend,
                is_positive: false,
            }
        }
    }

    fn add_delta(self, other: u128) -> u128 {
        if self.is_positive {
            self.delta + other
        } else {
            other - self.delta
        }
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/sharded_aggregator_service.rs (L69-99)
```rust
impl ops::Add for DeltaU128 {
    type Output = Self;

    fn add(self, rhs: Self) -> Self::Output {
        // the deltas are both positive or both negative, we add the deltas and keep the sign
        if self.is_positive == rhs.is_positive {
            return Self {
                delta: self.delta + rhs.delta,
                is_positive: self.is_positive,
            };
        }

        // the deltas are of opposite signs, we subtract the smaller from the larger and keep the
        // sign of the larger
        let (pos, neg) = if self.is_positive {
            (self.delta, rhs.delta)
        } else {
            (rhs.delta, self.delta)
        };

        if pos >= neg {
            return Self {
                delta: pos - neg,
                is_positive: true,
            };
        }
        Self {
            delta: neg - pos,
            is_positive: false,
        }
    }
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/sharded_aggregator_service.rs (L204-213)
```rust
    // The txn_outputs contain 'txn_total_supply' with
    // 'CrossShardStateViewAggrOverride::total_supply_aggr_base_val' as the base value.
    // The actual 'total_supply_base_val' is in the state_view.
    // The 'delta' for the shard/round is in aggr_total_supply_delta[round * num_shards + shard_id + 1]
    // For every txn_output, we have to compute
    //      txn_total_supply = txn_total_supply - CrossShardStateViewAggrOverride::total_supply_aggr_base_val + total_supply_base_val + delta
    // While 'txn_total_supply' is u128, the intermediate computation can be negative. So we use
    // DeltaU128 to handle any intermediate underflow of u128.
    let total_supply_base_val: u128 = get_state_value(&TOTAL_SUPPLY_STATE_KEY, state_view).unwrap();
    let base_val_delta = DeltaU128::get_delta(total_supply_base_val, TOTAL_SUPPLY_AGGR_BASE_VAL);
```

**File:** aptos-move/aptos-vm/src/sharded_block_executor/sharded_aggregator_service.rs (L229-236)
```rust
                        .for_each(|txn_output| {
                            if let Some(txn_total_supply) =
                                txn_output.write_set().get_total_supply()
                            {
                                txn_output.update_total_supply(
                                    delta_for_round.add_delta(txn_total_supply),
                                );
                            }
```

**File:** types/src/write_set.rs (L681-690)
```rust
    pub fn update_total_supply(&mut self, value: u128) {
        assert!(self
            .value_writes_mut()
            .write_set
            .insert(
                TOTAL_SUPPLY_STATE_KEY.clone(),
                WriteOp::legacy_modification(bcs::to_bytes(&value).unwrap().into())
            )
            .is_some());
    }
```
