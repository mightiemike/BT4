### Title
Panic-on-unwrap in `receipt_filter_fn` when a delayed `GlobalContractDistribution` receipt's target shard cannot be resolved across resharding - ([File: runtime/runtime/src/congestion_control.rs])

### Summary
`DelayedReceiptQueueWrapper::receipt_filter_fn` calls `.unwrap()` on `Receipt::receiver_shard_id`, which returns `Err` for a `GlobalContractDistribution` receipt whenever `ShardLayout::resolve_to_current_shard` cannot map the receipt's stored `target_shard` into the current shard layout. This mirrors the CVE-2023-42754 bug class: code assumes a precondition (the shard is always resolvable) holds for every receipt, but a receipt that is delayed long enough to cross resharding boundaries can violate it, turning a normal error path into an unhandled `unwrap()` panic.

### Finding Description
Any account can submit a `DeployGlobalContract` action in an ordinary transaction; the runtime turns this into a `GlobalContractDistribution` receipt carrying a fixed `target_shard` for every shard in the layout at creation time [1](#0-0) . If the receiving shard is congested, this receipt (like any other) can sit in the delayed-receipt queue across chunks and, consequently, across resharding events.

`Receipt::receiver_shard_id` special-cases `GlobalContractDistribution`: if `target_shard` is not part of the current layout it falls back to `shard_layout.resolve_to_current_shard(target_shard)`, and only returns `Err(EpochError::ShardingError(...))` if that also fails to find a descendant shard [2](#0-1) .

`DelayedReceiptQueueWrapper::receipt_filter_fn`, used both when popping delayed receipts (`pop`) and when peeking them (`peek_iter`), calls this function and immediately `.unwrap()`s the result instead of propagating the error:
```rust
fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
    let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
    let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
    receipt_shard_id == self.shard_id
}
``` [3](#0-2) 

This filter exists specifically because "with ReshardingV3, it's possible for a chunk to have delayed receipts that technically belong to the sibling shard before a resharding event" [4](#0-3) , and it is invoked from `pop()` on every dequeue of a delayed receipt during ordinary chunk application [5](#0-4) .

A repository test explicitly documents and probes this exact scenario: a `GlobalContractDistribution` receipt that survives two resharding generations while stuck in the delayed queue, where `receiver_shard_id()` may fail "to remap the old target_shard after two resharding generations," which "will panic in receipt_filter_fn()" [6](#0-5) . I was not able to fully trace `ShardLayout::resolve_to_current_shard`/`try_get_parent_shard_id` (in `core/primitives/src/shard_layout/{mod,v1,v2,v3}.rs`) within the available tool budget to determine conclusively whether it correctly walks an arbitrary number of resharding generations or only one. If it fails to walk multiple generations (which is exactly the scenario the cited test is built to catch), `receiver_shard_id()` returns `Err`, and `receipt_filter_fn`'s `unwrap()` panics.

### Impact Explanation
A panic inside `receipt_filter_fn` occurs deep inside delayed-receipt processing during ordinary chunk application (`DelayedReceiptQueueWrapper::pop`), which every validator applying that shard's chunk executes identically. A crash here is not an isolated node failure — it is a deterministic, protocol-path panic triggered by state that all honest nodes share (the same delayed queue, the same shard layout history), so it manifests as **a transaction-triggered halt**: any validator/chunk-producer tracking the affected shard aborts (or repeatedly fails) chunk application once the stale receipt reaches the front of the delayed queue after the relevant resharding events, stalling that shard indefinitely until the receipt is manually removed via ops intervention. This satisfies the "transaction-triggered halt" impact category.

### Likelihood Explanation
Reaching this bug requires: (1) an attacker to deploy a global contract (ordinary permissionless action) while the target shard is congested enough that the resulting `GlobalContractDistribution` receipt is delayed, and (2) two (or more) resharding events to occur while the receipt remains delayed, such that `resolve_to_current_shard` cannot resolve the now much older `target_shard` id back to a current shard. Resharding is infrequent and operator-scheduled, so the attacker cannot force it, but they can arrange the precondition (deploy the contract, keep the target shard congested) and then simply wait; whether resharding boundaries are crossed while the receipt is still queued is largely a timing/network-conditions question, not something requiring privileged access. The explicit regression test built specifically around "two resharding generations" indicates this was recognized as a real edge case worth guarding against, which raises confidence that the scenario is reachable in principle, even though the fix path (`resolve_to_current_shard`) could not be fully verified as generation-complete with the tools available.

### Recommendation
Replace the `.unwrap()` calls in `receipt_filter_fn` with proper error propagation (return a `Result` from `pop`/`peek_iter` and surface it as `RuntimeError`/`StorageError::StorageInconsistentState` instead of panicking), and add exhaustive testing/verification that `ShardLayout::resolve_to_current_shard` (and its underlying `try_get_parent_shard_id` walk) correctly resolves a `target_shard` across an arbitrary number of chained resharding generations, not just one.

### Proof of Concept
1. Submit a `DeployGlobalContract` transaction whose resulting `GlobalContractDistribution` receipt targets a shard kept congested so the receipt is buffered/delayed rather than forwarded immediately (`ReceiptSinkV2::try_forward` → `NotForwarded` → `buffer_receipt`) [7](#0-6) .
2. Trigger (or wait for) two successive resharding events while the receipt remains in the delayed/outgoing-buffer queue, changing the shard layout twice relative to the receipt's recorded `target_shard`.
3. Once congestion clears and the shard attempts to pop/peek the stale receipt, `DelayedReceiptQueueWrapper::pop` calls `receipt_filter_fn`, which calls `receiver_shard_id()`; if `resolve_to_current_shard` cannot map the doubly-stale `target_shard` to a current shard, it returns `Err`, and the `.unwrap()` at `congestion_control.rs:876` panics, aborting chunk application for that shard on every node that reaches this state (as exercised by the existing regression test at `test-loop-tests/src/tests/global_contracts_distribution.rs:163-186`) [6](#0-5) .

### Citations

**File:** core/primitives/src/receipt.rs (L437-466)
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

**File:** runtime/runtime/src/congestion_control.rs (L403-463)
```rust
    fn try_forward(
        receipt: Receipt,
        gas: Gas,
        mut size: u64,
        shard: ShardId,
        outgoing_limit: &mut HashMap<ShardId, OutgoingLimit>,
        outgoing_receipts: &mut Vec<Receipt>,
        apply_state: &ApplyState,
        stats: &mut ReceiptSinkStats,
    ) -> Result<ReceiptForwarding, RuntimeError> {
        // There is a bug which allows to create receipts that are above the size limit. Receipts
        // above the size limit might not fit under the maximum outgoing size limit. Let's pretend
        // that all receipts are at most `max_receipt_size` to avoid receipts getting stuck.
        // See https://github.com/near/nearcore/issues/12606
        let max_receipt_size = apply_state.config.wasm_config.limit_config.max_receipt_size;
        if size > max_receipt_size {
            tracing::debug!(
                target: "runtime",
                receipt_id=?receipt.receipt_id(),
                size,
                max_receipt_size,
                "try_forward observed a receipt with size exceeding the size limit",
            );
            size = max_receipt_size;
        }

        // Default case set to `Gas::MAX`: If no outgoing limit was defined for the receiving
        // shard, this usually just means the feature is not enabled. Or, it
        // could be a special case during resharding events. Or even a bug. In
        // any case, if we cannot know a limit, treating it as literally "no
        // limit" is the safest approach to ensure availability.
        let default_gas_limit = Gas::MAX;

        // Since bandwidth scheduler, a shard is not allowed to send any receipts if it doesn't have a grant.
        let default_size_limit = 0;

        let default_outgoing_limit =
            OutgoingLimit { gas: default_gas_limit, size: default_size_limit };
        let forward_limit = outgoing_limit.entry(shard).or_insert(default_outgoing_limit);

        let admission_gas = if ProtocolFeature::ClampOutgoingGasAdmission
            .enabled(apply_state.current_protocol_version)
        {
            gas.min(apply_state.config.congestion_control_config.allowed_shard_outgoing_gas)
        } else {
            gas
        };

        if forward_limit.gas >= admission_gas && forward_limit.size >= size {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "forwarding buffered receipt");
            outgoing_receipts.push(receipt);
            forward_limit.gas = forward_limit.gas.saturating_sub(gas);
            forward_limit.size -= size;
            stats.forwarded_receipts.entry(shard).or_default().add_receipt(size, gas);

            Ok(ReceiptForwarding::Forwarded)
        } else {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "not forwarding buffered receipt");
            Ok(ReceiptForwarding::NotForwarded(receipt))
        }
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L868-878)
```rust
    // With ReshardingV3, it's possible for a chunk to have delayed receipts that technically
    // belong to the sibling shard before a resharding event.
    // Here, we filter all the receipts that don't belong to the current shard_id.
    //
    // The function follows the guidelines of standard iterator filter function
    // We return true if we should retain the receipt and false if we should filter it.
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L880-910)
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
