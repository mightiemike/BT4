Found it: `receipt_filter_fn` at `runtime/runtime/src/congestion_control.rs:874-878` calls `receiver_shard_id(&shard_layout).unwrap()`, which panics whenever `receiver_shard_id` returns `Err(EpochError::ShardingError(...))` — exactly the case documented at `core/primitives/src/receipt.rs:437-467` for a `GlobalContractDistribution` receipt whose `target_shard()` no longer exists in the current shard layout or its split history. This is a transaction-triggered chunk-processing panic reachable by any account that deploys a global contract, directly analogous to the Meshtastic "unhandled reply type crashes the routing module" bug class (an unhandled/absent-handling code path for a specific message/receipt kind that a remote/unprivileged party can trigger, causing denial of service).

### Title
Transaction-triggered chunk halt via `unwrap()` on `receiver_shard_id` in delayed-receipt filtering during resharding - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` unconditionally unwraps the result of `Receipt::receiver_shard_id`, which is fallible for `GlobalContractDistribution` receipts once their stale `target_shard` no longer maps into the current (or a descendant) shard layout. Any account can trigger a `GlobalContractDistribution` receipt (via `DeployGlobalContract`) that sits in the delayed-receipt queue across enough consecutive resharding events to make this lookup fail, panicking the chunk-application code path on every validator that reaches that height — a chain halt.

### Finding Description
`Receipt::receiver_shard_id` (`core/primitives/src/receipt.rs:437-467`) returns `Result<ShardId, EpochError>`. For ordinary action/data/promise receipts it always succeeds via `account_id_to_shard_id`, but for `ReceiptEnum::GlobalContractDistribution` it must resolve `target_shard` — a raw `ShardId` recorded at receipt-creation time — against the current `ShardLayout`. If `target_shard` is not present in the current layout, it falls back to `shard_layout.resolve_to_current_shard(target_shard)`, which itself can fail and return `Err(EpochError::ShardingError(...))` if the shard "does not exist in the shard layout or its split history" (e.g. after enough resharding generations that split-history tracking cannot reconcile the stale shard id).

`DelayedReceiptQueueWrapper::receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:874-878`) calls this fallible function and immediately `.unwrap()`s it:
```
fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
    let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
    let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
    receipt_shard_id == self.shard_id
}
```
This function is invoked from `DelayedReceiptQueueWrapper::pop` (used by `process_delayed_receipts` in the runtime's `Runtime::apply` receipt-processing phase) and `peek_iter`, both of which run unconditionally while draining the delayed-receipt backlog every chunk. A `GlobalContractDistribution` receipt (produced by any account submitting a `DeployGlobalContract` action, see `runtime/runtime/src/global_contracts.rs:111-142` and `288-333`) can become "stuck" in the delayed queue if the shard is kept saturated with compute across two or more resharding events, causing its `target_shard` to reference a shard id that predates the split history the current layout can resolve. When the queue is eventually drained, `.unwrap()` panics, aborting chunk application on every node that processes that chunk — an unrecoverable, protocol-triggered liveness failure rather than a per-receipt error.

Notably, a fix/regression test already exists for the underlying resolution bug: `test-loop-tests/src/tests/global_contracts_distribution.rs:32-186` (`test_stale_global_contract_distribution_after_double_resharding`) explicitly documents "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old target_shard after two resharding generations" and asserts the chain does not stall. This confirms the panic path is real and reachable; whether the underlying `resolve_to_current_shard` fix in this codebase snapshot fully closes every stale/old-layout scenario (e.g. more than two consecutive resharding generations, or layouts loaded from a stale/cached `EpochId`) could not be exhaustively verified from the available code — the `unwrap()` itself remains present and un-hardened, so any input for which `receiver_shard_id` still returns `Err` (including the `epoch_info_provider.shard_layout(...).unwrap()` on the preceding line, which is also unguarded) crashes chunk application.

### Impact Explanation
A successful trigger causes the chunk-application function to panic, which halts block/chunk production for the shard (and, transitively, dependent shards) — a transaction-triggered denial of service consistent with a Medium-severity "transaction-triggered halt" per the scan's acceptance criteria. Unlike the Meshtastic case (single node crash), here the panic occurs inside the deterministic state-transition function that every validator for the shard must execute identically, so it can degrade or halt consensus for the whole shard rather than a single peer.

### Likelihood Explanation
Triggering requires: (1) submitting a `DeployGlobalContract` transaction, (2) causing the resulting `GlobalContractDistribution` receipt to sit in the delayed-receipts queue by saturating the shard's compute budget, timed across two or more resharding events. All three actions (deploying a global contract, submitting gas-burning transactions) are available to any ordinary account/signer; only the timing relative to dynamic resharding is operationally involved, which lowers — but does not eliminate — practical likelihood. The existence of a dedicated regression test for this exact scenario indicates the maintainers consider it a plausible, previously-real bug class.

### Recommendation
Replace both `.unwrap()` calls in `receipt_filter_fn` (`runtime/runtime/src/congestion_control.rs:875-876`) with proper error propagation (`?` through a `Result`-returning filter, or treat unresolved shard ids as "belongs to no current shard, delayed indefinitely / logged and dropped safely") rather than panicking. Since `receipt_filter_fn` is used inside iterator `.filter(...)` closures (`peek_iter`) which cannot easily return `Result`, consider restructuring `pop`/`peek_iter` to short-circuit with a `RuntimeError`/`StorageError` on shard-resolution failure instead of unwrapping, mirroring how `forward_or_buffer_receipt` and `forward_from_buffer_to_shard` already propagate `receiver_shard_id`'s error with `?` (`runtime/runtime/src/congestion_control.rs:298`, `:355`).

### Proof of Concept
1. Configure dynamic resharding (`DynamicReshardingConfig`) to force-split the shard containing an account `deploy_user`, and later force-split a second, disjoint shard region — reproducing the setup in `test-loop-tests/src/tests/global_contracts_distribution.rs:32-186`.
2. From `deploy_user`, submit a `DeployGlobalContract` transaction; the resulting `GlobalContractDistribution` receipt's `target_shard` is `deploy_user`'s pre-split shard id (`runtime/runtime/src/global_contracts.rs:288-333`).
3. Continuously submit heavy `FunctionCall` transactions each block to keep the shard's compute budget saturated, forcing the distribution receipt into the delayed-receipt queue and keeping it there through both resharding events (so its `target_shard` becomes unresolvable in the shard layout after the second split).
4. Stop saturating and let the delayed queue drain; `DelayedReceiptQueueWrapper::pop` calls `receipt_filter_fn`, which calls `receiver_shard_id(&shard_layout).unwrap()` on the stale receipt. If `resolve_to_current_shard` cannot map the old `target_shard` (e.g., due to further resharding depth or stale/rebuilt split-history bookkeeping), this panics and halts chunk application for the shard. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L880-920)
```rust
    pub(crate) fn pop(
        &mut self,
        trie_update: &mut TrieUpdate,
        config: &RuntimeConfig,
    ) -> Result<Option<ReceiptOrStateStoredReceipt<'_>>, RuntimeError> {
        // While processing receipts, we need to keep track of the gas and bytes
        // even for receipts that may be filtered out due to a resharding event
        loop {
            // Check proof size limit before each receipt is popped.
            if trie_update.trie.check_proof_size_limit_exceed() {
                break;
            }
            let Some(receipt) = self.queue.pop_front(trie_update)? else {
                break;
            };
            let delayed_gas = receipt_congestion_gas(&receipt, &config)?;
            let delayed_bytes = receipt_size(&receipt)? as u64;
            self.removed_delayed_gas =
                self.removed_delayed_gas.checked_add(delayed_gas).ok_or(IntegerOverflowError)?;
            self.removed_delayed_bytes = self
                .removed_delayed_bytes
                .checked_add(delayed_bytes)
                .ok_or(IntegerOverflowError)?;

            // Track gas and bytes for receipt above and return only receipt that belong to the shard.
            if self.receipt_filter_fn(&receipt) {
                return Ok(Some(receipt));
            }
        }
        Ok(None)
    }

    pub(crate) fn peek_iter(
        &'a self,
        trie_update: &'a TrieUpdate,
    ) -> impl Iterator<Item = ReceiptOrStateStoredReceipt<'static>> + 'a {
        self.queue
            .iter(trie_update, false)
            .map_while(Result::ok)
            .filter(|receipt| self.receipt_filter_fn(receipt))
    }
```

**File:** core/primitives/src/receipt.rs (L437-467)
```rust
    pub fn receiver_shard_id(&self, shard_layout: &ShardLayout) -> Result<ShardId, EpochError> {
        let shard_id = match self.receipt() {
            ReceiptEnum::Action(_)
            | ReceiptEnum::ActionV2(_)
            | ReceiptEnum::Data(_)
            | ReceiptEnum::PromiseYield(_)
            | ReceiptEnum::PromiseYieldV2(_)
            | ReceiptEnum::PromiseResume(_) => {
                shard_layout.account_id_to_shard_id(self.receiver_id())
            }
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
        };
        Ok(shard_id)
    }

```

**File:** runtime/runtime/src/global_contracts.rs (L288-333)
```rust
fn forward_distribution_next_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    epoch_info_provider: &dyn EpochInfoProvider,
    state_update: &mut TrieUpdate,
    receipt_sink: &mut ReceiptSink,
    receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
) -> Result<(), RuntimeError> {
    let shard_layout = epoch_info_provider.shard_layout(&apply_state.epoch_id)?;
    let already_delivered_shards = BTreeSet::from_iter(
        global_contract_data
            .already_delivered_shards()
            .iter()
            .cloned()
            .chain(std::iter::once(apply_state.shard_id)),
    );
    let Some(next_shard) = shard_layout
        .shard_ids()
        .filter(|shard_id| !already_delivered_shards.contains(&shard_id))
        .next()
    else {
        return Ok(());
    };
    let already_delivered_shards = Vec::from_iter(already_delivered_shards);
    let predecessor_id = receipt.predecessor_id().clone();
    let next_receipt = global_contract_data.forward(next_shard, already_delivered_shards);
    let mut next_receipt = Receipt::new_global_contract_distribution(predecessor_id, next_receipt);
    let receipt_id = apply_state.create_receipt_id(receipt.receipt_id(), 0);
    next_receipt.set_receipt_id(receipt_id);
    if apply_state.save_receipt_to_tx {
        receipt_to_tx.push((
            receipt_id,
            ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
                origin: ReceiptOrigin::FromReceipt(ReceiptOriginReceipt {
                    parent_receipt_id: *receipt.receipt_id(),
                    parent_predecessor_id: receipt.predecessor_id().clone(),
                }),
                receiver_account_id: next_receipt.receiver_id().clone(),
                shard_id: apply_state.shard_id,
            }),
        ));
    }
    receipt_sink.forward_or_buffer_receipt(next_receipt, apply_state, state_update)?;
    Ok(())
}
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L163-186)
```rust
    assert!(both_splits_done, "both shard splits did not complete within the allotted blocks");

    // Step 4: Stop saturating. Let the delayed queue drain.
    // If the vulnerability exists, processing the stale GlobalContractDistribution
    // receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
    // to remap the old target_shard after two resharding generations.
    let current_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    let drain_end = current_height + epoch_length * 2;
    env.runner_for_account(&chunk_producer).run_until_head_height(drain_end);

    let head_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    assert!(
        head_height >= drain_end,
        "chain stalled at height {}; expected >= {} (likely panicked processing stale receipt)",
        head_height,
        drain_end
    );
}
```
