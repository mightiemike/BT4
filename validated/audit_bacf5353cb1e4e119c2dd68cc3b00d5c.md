## Title
Underpriced L1→L2 Messages Allow Cheap DoS of Block Building Capacity - (File: `crates/blockifier/src/transaction/l1_handler_transaction.rs`)

### Summary
The `OptimismPortal` bug class ("pay a tiny L1 fee to consume the entire per-block guaranteed L2 gas quota, starving legitimate users") has a direct analog in the sequencer: L1 handler transactions (Starknet's L1→L2 message consumers) are only required to have paid a *non-zero* fee on L1 — the fee is never compared to the actual L2 resources the message is allowed to consume. Combined with a per-tx resource ceiling (`l1_handler_max_amount_bounds`) that is a large fraction of (or, in older versions, larger than) the whole block's bouncer capacity, an attacker can send arbitrarily cheap L1→L2 messages that each burn a large slice of the block's shared `sierra_gas`/`proving_gas`/`receipt_l2_gas` budget, crowding out all other transactions (including other users' deposits/messages and ordinary L2 transactions) from the block.

### Finding Description
When an `L1HandlerTransaction` executes, the sequencer bounds its resource usage by `l1_handler_max_amount_bounds` and, after execution, only checks that *some* fee was paid on L1 — not that the fee is commensurate with resources consumed: [1](#0-0) 

The comment even documents this as an intentional-but-weak placeholder: *"For now, assert only that any amount of fee was paid... The error message still indicates the required fee."* [2](#0-1) 

The per-transaction resource ceiling for these messages is `l1_handler_max_amount_bounds`, which in several versioned-constants files is a very large share of (or exceeds) the block's total bouncer capacity:
- `blockifier_versioned_constants_0_13_0.json` / `0_13_2_1.json` / `0_13_3.json`: `l1_gas`/`l1_data_gas`/`l2_gas` bounds are each `10,000,000,000`, i.e. *larger* than the default block-wide `sierra_gas`/`proving_gas` cap of `5,000,000,000` and `receipt_l2_gas` cap of `5,800,000,000` [3](#0-2) [4](#0-3) 
- Even the tightened `0.14.0` bound (`l2_gas: 100,000,000`) still lets a handful of spam messages consume the entire block's `sierra_gas`/`receipt_l2_gas` budget (`5,000,000,000`/`5,800,000,000`) [5](#0-4) 

These L1-handler transactions are fed into block building via `ProposeTransactionProvider`, which reserves a fixed quota of slots (`max_l1_handler_txs_per_block_proposal`, e.g. `200` in one deployment config) per block *before* falling back to ordinary mempool transactions: [6](#0-5) [7](#0-6) 

Once execution resources accumulate in the block's shared `Bouncer`, any subsequent transaction (L1 handler or ordinary invoke/declare) that would push accumulated weights over the configured `block_max_capacity` is rejected with `BlockFull`, and the block closes early: [8](#0-7) 

Because the sequencer's only fee gate for L1 handlers is "fee paid on L1 is nonzero" — not "fee is proportional to consumed gas" — an attacker can:
1. Send `sendMessageToL2` calls on L1 with a minimal `value` (e.g. 1 wei), targeting an L1-handler entry point crafted to consume near the maximal allowed `l1_handler_max_amount_bounds` of `sierra_gas`/`l2_gas` per message.
2. Repeat this a small number of times (bounded by `max_l1_handler_txs_per_block_proposal`) to exhaust the block's `sierra_gas`, `proving_gas`, or `receipt_l2_gas` bouncer budget for negligible L1 cost.
3. Cause the block builder to hit `BlockFull` for the remainder of the block, excluding all other pending transactions (including legitimate deposits/messages and unrelated L2 user transactions) from that block.

This directly mirrors the reported `ResourceMetering` DoS pattern: a resource-metering gate exists, but the fee required to consume it is decoupled from the actual amount of guaranteed capacity consumed, letting an attacker cheaply and repeatedly deny the shared resource to everyone else.

### Impact Explanation
This is a network-unable-to-confirm-new-transactions condition: an attacker can repeatedly and cheaply monopolize the block's execution/proving-gas budget via minimally-paid L1→L2 messages, starving all other transactions (ordinary invokes, declares, and legitimate L1→L2 messages/deposits) from being included in the block. This is a Medium-severity availability/DoS impact on the sequencer's block-building pipeline, directly reachable by any L1 message sender without special privileges.

### Likelihood Explanation
Likelihood is high: sending `sendMessageToL2` with `value=1` wei is standard, unprivileged, and inexpensive on L1; crafting an L1-handler entry point (or targeting any deployed contract with an expensive `l1_handle`-style callback) that consumes close to `l1_handler_max_amount_bounds` in Sierra gas requires no special access. The check that gates fee sufficiency is explicitly a placeholder ("assert only that any amount of fee was paid") rather than a real economic guard.

### Recommendation
Enforce that `paid_fee_on_l1` is proportional to (at least covers) the actual L2 gas vector consumed by the L1 handler transaction, rather than just checking `paid_fee != Fee(0)`, in `crates/blockifier/src/transaction/l1_handler_transaction.rs`. Additionally, consider tightening `l1_handler_max_amount_bounds` relative to block-wide bouncer capacity so that no small number of L1 handler transactions can dominate a block's `sierra_gas`/`proving_gas`/`receipt_l2_gas` budget, and/or apply a dedicated per-block sub-quota for L1-handler resource consumption independent of ordinary transaction capacity.

### Proof of Concept
1. Deploy an L2 contract with an `l1_handle` entry point that performs heavy computation (loops/storage writes) up to close to `l1_handler_max_amount_bounds.l2_gas`.
2. From L1, call `sendMessageToL2` targeting this contract/entry point with `value = 1` (minimal wei fee), repeated `N` times where `N * per-tx-gas ≈ block_max_capacity.sierra_gas` (e.g., a few dozen calls given the `0.13.x` bounds, or up to `max_l1_handler_txs_per_block_proposal` calls given `0.14.0` bounds).
3. Observe that the sequencer's `l1_handler_transaction.rs::execute_raw` accepts each message because `paid_fee_on_l1 != Fee(0)` — see the check at lines 103-113 — regardless of the disproportionate gas consumed.
4. Observe the block builder's `Bouncer::try_update` returning `BlockFull` for subsequent legitimate transactions once the accumulated weights from the spam L1 handlers exceed `block_max_capacity`, per `crates/blockifier/src/bouncer.rs` lines 664-690, causing normal user transactions to be excluded from the block for that round at a total attacker cost of only `N` wei.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L92-113)
```rust
                // Enforce resource bounds.
                let fee_check_report = FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &l1_handler_bounds,
                    &receipt.gas,
                );
                match fee_check_report {
                    Ok(()) => {
                        // Post-execution check passed, commit the execution.
                        execution_state.commit();
                        // TODO(Arni): Consider removing this check. It is covered by the starknet
                        // core contract.
                        let paid_fee = self.paid_fee_on_l1;
                        // For now, assert only that any amount of fee was paid.
                        // The error message still indicates the required fee.
                        if paid_fee == Fee(0) {
                            return Err(TransactionExecutionError::TransactionFeeError(Box::new(
                                TransactionFeeError::InsufficientFee {
                                    paid_fee,
                                    actual_fee: receipt.fee,
                                },
                            )));
                        }
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_13_0.json (L362-367)
```json
        "l1_handler_version": 0,
        "l1_handler_max_amount_bounds": {
            "l1_gas": 10000000000,
            "l1_data_gas": 10000000000,
            "l2_gas": 10000000000
        },
```

**File:** crates/blockifier/src/bouncer.rs (L216-230)
```rust
impl Default for BouncerWeights {
    // TODO(Yael): update the default values once the actual values are known.
    fn default() -> Self {
        Self {
            l1_gas: 2500000,
            message_segment_length: 3700,
            n_events: 5000,
            n_txs: 600,
            state_diff_size: 4000,
            sierra_gas: GasAmount(5000000000),
            proving_gas: GasAmount(5000000000),
            // NOTE: Must stay in sync with orchestrator_versioned_constants' max_block_size.
            receipt_l2_gas: GasAmount(5800000000),
        }
    }
```

**File:** crates/blockifier/src/bouncer.rs (L664-690)
```rust
        // Check if the transaction can fit the current block available capacity.
        let err_msg = format!(
            "Addition overflow. Transaction weights: {tx_bouncer_weights:?}, block weights: {:?}.",
            self.get_bouncer_weights()
        );
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
        if !self.bouncer_config.has_room(next_accumulated_weights) {
            let exceeded_weights =
                self.bouncer_config.get_exceeded_weights(next_accumulated_weights);
            log::debug!(
                "Transaction cannot be added to the current block, block capacity reached; \
                 transaction weights: {:?}, block weights: {:?}. Block max capacity reached on \
                 fields: {}",
                tx_weights.bouncer_weights,
                self.get_bouncer_weights(),
                exceeded_weights
            );
            // Record the block-full metric only once per block. Later candidate txs that also do
            // not fit (subsequent chunks / executor invocations share this bouncer) would otherwise
            // inflate the counter into a per-rejected-tx count instead of a per-block count.
            if !self.block_full_recorded {
                record_exceeded_bouncer_resources(&exceeded_weights);
                self.block_full_recorded = true;
            }
            Err(TransactionExecutorError::BlockFull)?
        }
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_0.json (L184-189)
```json
        "l1_handler_version": 0,
        "l1_handler_max_amount_bounds": {
            "l1_gas": 40000,
            "l1_data_gas": 20000,
            "l2_gas": 100000000
        },
```

**File:** crates/apollo_batcher/src/transaction_provider.rs (L128-154)
```rust
    async fn get_txs(&mut self, n_txs: usize) -> TransactionProviderResult<NextTxs> {
        assert!(n_txs > 0, "The number of transactions requested must be greater than zero.");
        let mut txs = vec![];
        if self.phase == TxProviderPhase::L1 {
            let n_l1handler_txs_to_get =
                min(self.max_l1_handler_txs_per_block - self.n_l1handler_txs_so_far, n_txs);
            let mut l1handler_txs = self.get_l1_handler_txs(n_l1handler_txs_to_get).await?;
            self.n_l1handler_txs_so_far += l1handler_txs.len();

            // Determine whether we need to switch to mempool phase.
            let no_more_l1handler_in_provider = l1handler_txs.len() < n_l1handler_txs_to_get;
            let reached_max_l1handler_txs_in_block =
                self.n_l1handler_txs_so_far == self.max_l1_handler_txs_per_block;
            if no_more_l1handler_in_provider || reached_max_l1handler_txs_in_block {
                self.phase = TxProviderPhase::Mempool;
            }

            txs.append(&mut l1handler_txs);
            if txs.len() == n_txs {
                return Ok(txs);
            }
        }

        let mut mempool_txs = self.get_mempool_txs(n_txs - txs.len()).await?;
        txs.append(&mut mempool_txs);
        Ok(txs)
    }
```

**File:** crates/apollo_deployments/resources/app_configs/batcher_config.json (L48-48)
```json
  "batcher_config.static_config.max_l1_handler_txs_per_block_proposal": 200,
```
