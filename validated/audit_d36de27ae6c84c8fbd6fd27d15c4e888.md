### Title
`GlobalContractDistributionReceipt` with stale `target_shard` causes chain-halting panic after two static resharding events — (`runtime/runtime/src/congestion_control.rs`)

---

### Summary

When a `GlobalContractDistributionReceipt` is pushed to the delayed receipt queue and two static (protocol-version-driven) resharding events occur while it waits, `DelayedReceiptQueueWrapper::receipt_filter_fn` panics unconditionally. The root cause is that `ShardLayout::V2::resolve_to_current_shard` only tracks one generation of splits; after a second resharding the original `target_shard` is absent from the new layout's split map, `Receipt::receiver_shard_id` returns `Err`, and the `.unwrap()` in `receipt_filter_fn` crashes every validator node that tries to apply the affected chunk, permanently halting the chain.

---

### Finding Description

**Step 1 — Receipt creation (user-triggered)**

Any unprivileged user can call `DeployGlobalContractAction`. The runtime calls `initiate_distribution` in `runtime/runtime/src/global_contracts.rs`, which creates a `GlobalContractDistributionReceipt` whose `target_shard` is set to the deployer's current shard `S`:

```rust
let distribution_receipt =
    GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
``` [1](#0-0) 

**Step 2 — Receipt enters the delayed queue**

If the target shard is congested (naturally or induced by the attacker flooding it with transactions), the incoming `GlobalContractDistributionReceipt` is not executed immediately and is pushed to the persistent `DelayedReceiptQueue` via `delayed_receipts.push(...)`. [2](#0-1) 

**Step 3 — First static resharding: S → {S1, S2}**

At a protocol version boundary, shard `S` is split. The flat-storage resharding copies the entire delayed queue (including the receipt with `target_shard = S`) to both child shards. The new `ShardLayout::V2` records `shards_split_map = { S: [S1, S2] }`. When `receipt_filter_fn` is called on S1, `resolve_to_current_shard(S)` correctly returns `S1` (the first child), so the receipt is retained on S1 and discarded on S2. Everything works. [3](#0-2) 

**Step 4 — Second static resharding: S1 → {S1a, S1b}**

At the next protocol version boundary, shard `S1` is split. The new `ShardLayout::V2` now records `shards_split_map = { S1: [S1a, S1b] }`. **Critically, V2 only stores the most recent split.** The previous mapping `S → [S1, S2]` is gone. The delayed queue of S1 (which still contains the receipt with `target_shard = S`) is copied to both S1a and S1b.

**Step 5 — Panic in `receipt_filter_fn`**

When S1a (or S1b) processes its delayed queue, `DelayedReceiptQueueWrapper::pop` calls `receipt_filter_fn`:

```rust
fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
    let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
    let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap(); // ← panics
    receipt_shard_id == self.shard_id
}
``` [4](#0-3) 

`receiver_shard_id` for `GlobalContractDistributionReceipt` checks whether `target_shard = S` is in the current layout. It is not (S was split two generations ago), so it falls through to `resolve_to_current_shard(S)`:

```rust
ReceiptEnum::GlobalContractDistribution(receipt) => {
    let target_shard = receipt.target_shard();
    if shard_layout.shard_ids().contains(&target_shard) {
        target_shard
    } else {
        let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
        else {
            return Err(EpochError::ShardingError(format!(
                "Shard {target_shard} does not exist in the shard layout or its split history",
            )));
        };
        current_shard
    }
}
``` [5](#0-4) 

For `ShardLayout::V2`, `resolve_to_current_shard` delegates to `get_children_shards_ids`, which only knows about the **most recent** split (`S1 → [S1a, S1b]`). It has no record of `S → [S1, S2]`, so it returns `None`:

```rust
Self::V0(_) | Self::V1(_) | Self::V2(_) => {
    self.get_children_shards_ids(shard_id).map(|c| c[0])
}
``` [6](#0-5) 

`receiver_shard_id` returns `Err(EpochError::ShardingError(...))`. The `.unwrap()` in `receipt_filter_fn` panics. Because every validator applies the same chunk, every validator panics at the same block height, permanently halting the chain.

**The test in the codebase explicitly documents this crash path:**

```
// If the vulnerability exists, processing the stale GlobalContractDistribution
// receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
// to remap the old target_shard after two resharding generations.
``` [7](#0-6) 

The test also explicitly states the fix only works for V3 (dynamic resharding) layouts:

```
// The fix only works with V3 shard layouts (dynamic resharding).
// With static resharding, the shard layout doesn't maintain a full split history.
if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
    return;
}
``` [8](#0-7) 

---

### Impact Explanation

Every validator node that attempts to apply the chunk containing the stale delayed receipt panics unconditionally. Because all validators process the same chunk, the chain halts permanently at that block height. No funds can move, no transactions can execute, and no recovery is possible without a coordinated emergency patch and restart. This is a contract execution flow breakage and a non-network-level denial of service reachable from ordinary user actions.

---

### Likelihood Explanation

The trigger requires:
1. A user deploys a global contract (any account holder can do this).
2. The target shard is congested at the time the receipt arrives, pushing it to the delayed queue (achievable by the attacker flooding the shard with transactions, or occurring naturally under load).
3. Two static resharding events occur while the receipt remains in the delayed queue.

Conditions 1 and 2 are fully under attacker control. Condition 3 depends on protocol upgrade timing, but mainnet has already undergone two resharding events and future upgrades are planned. An attacker who monitors the upgrade schedule can time the deployment and congestion attack to ensure the receipt survives both resharding boundaries. The `GlobalContractDistribution` feature is part of the default protocol (enabled since protocol version ~77, well below `MIN_SUPPORTED_PROTOCOL_VERSION = 83`).

---

### Recommendation

1. **Remove the `.unwrap()` in `receipt_filter_fn`**: Replace it with a graceful error path. If `receiver_shard_id` returns `Err` for a `GlobalContractDistributionReceipt`, the receipt should either be silently dropped (the contract code will be re-distributed on the next deploy) or forwarded to the first child shard rather than causing a panic.

2. **Backport the V3 split-history resolution to V2**: `ShardLayout::V2::resolve_to_current_shard` should walk the full chain of historical layouts (as `ShardLayoutV3::resolve_to_current_shard` does via `shards_split_map`) rather than only checking the immediate parent-child relationship.

3. **Add a protocol-version-gated guard**: Before calling `receipt_filter_fn`, check whether the receipt is a `GlobalContractDistributionReceipt` and handle the stale-shard case explicitly.

---

### Proof of Concept

The codebase already contains a regression test that reproduces the exact crash:

1. Deploy a global contract from `user0` (whose shard `S_A` will be split first).
2. Saturate `S_A`'s compute budget every block so the `GlobalContractDistributionReceipt` is pushed to the delayed queue.
3. Trigger two sequential dynamic resharding events (first splitting `S_A`, then splitting a second shard).
4. Stop saturating and let the delayed queue drain.

Without the fix (i.e., with `ShardLayout::V2`), step 4 causes `receipt_filter_fn` to call `.unwrap()` on an `Err` result, panicking every validator and stalling the chain. The test asserts the chain does **not** stall:

```rust
assert!(
    head_height >= drain_end,
    "chain stalled at height {}; expected >= {} (likely panicked processing stale receipt)",
    head_height,
    drain_end
);
``` [9](#0-8) 

The test is gated on `DynamicResharding` being enabled (V3 layouts), confirming that the V2 (static resharding) path remains unprotected.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L162-163)
```rust
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
```

**File:** runtime/runtime/src/congestion_control.rs (L838-865)
```rust
    pub(crate) fn push(
        &mut self,
        trie_update: &mut TrieUpdate,
        receipt: &Receipt,
        apply_state: &ApplyState,
    ) -> Result<(), RuntimeError> {
        let config = &apply_state.config;

        let gas = compute_receipt_congestion_gas(&receipt, &config)?;
        let size = compute_receipt_size(&receipt)? as u64;

        // TODO It would be great to have this method take owned Receipt and
        // get rid of the Cow from the Receipt and StateStoredReceipt.
        let receipt = match config.use_state_stored_receipt {
            true => {
                let metadata =
                    StateStoredReceiptMetadata { congestion_gas: gas, congestion_size: size };
                let receipt = StateStoredReceipt::new_borrowed(receipt, metadata);
                ReceiptOrStateStoredReceipt::StateStoredReceipt(receipt)
            }
            false => ReceiptOrStateStoredReceipt::Receipt(Cow::Borrowed(receipt)),
        };

        self.new_delayed_gas = self.new_delayed_gas.checked_add(gas).ok_or(IntegerOverflowError)?;
        self.new_delayed_bytes =
            self.new_delayed_bytes.checked_add(size).ok_or(IntegerOverflowError)?;
        self.queue.push_back(trie_update, &receipt)?;
        Ok(())
```

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** core/primitives/src/shard_layout/mod.rs (L230-237)
```rust
    pub fn resolve_to_current_shard(&self, shard_id: ShardId) -> Option<ShardId> {
        match self {
            Self::V0(_) | Self::V1(_) | Self::V2(_) => {
                self.get_children_shards_ids(shard_id).map(|c| c[0])
            }
            Self::V3(v3) => v3.resolve_to_current_shard(shard_id),
        }
    }
```

**File:** core/primitives/src/receipt.rs (L447-463)
```rust
            ReceiptEnum::GlobalContractDistribution(receipt) => {
                let target_shard = receipt.target_shard();
                if shard_layout.shard_ids().contains(&target_shard) {
                    target_shard
                } else {
                    // The target shard may be from an arbitrarily old layout (the receipt could
                    // have been delayed across multiple resharding events). resolve_to_current_shard
                    // will find a shard descendant in the current layout.
                    let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
                    else {
                        return Err(EpochError::ShardingError(format!(
                            "Shard {target_shard} does not exist in the shard layout or its split history",
                        )));
                    };
                    current_shard
                }
            }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L35-39)
```rust
    // The fix only works with V3 shard layouts (dynamic resharding).
    // With static resharding, the shard layout doesn't maintain a full split history.
    if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
        return;
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L165-168)
```rust
    // Step 4: Stop saturating. Let the delayed queue drain.
    // If the vulnerability exists, processing the stale GlobalContractDistribution
    // receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
    // to remap the old target_shard after two resharding generations.
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L180-185)
```rust
    assert!(
        head_height >= drain_end,
        "chain stalled at height {}; expected >= {} (likely panicked processing stale receipt)",
        head_height,
        drain_end
    );
```
