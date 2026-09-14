Based on the investigation, nearcore's runtime receipt-processing pipeline contains an analogous pattern: a per-item validation failure inside a loop over a dynamically-sized, attacker-influenced list of items (incoming/delayed receipts) is propagated as a hard `RuntimeError` that aborts the *entire* `apply()` call, rather than being isolated to the offending item — mirroring the Blueberry bug where a single unpriceable reward token reverted the whole `getPositionValue` call and blocked liquidations for everyone.

### Title
Single invalid incoming/delayed receipt aborts entire chunk `apply()`, halting all other receipts/transactions in the chunk - (File: runtime/runtime/src/lib.rs)

### Summary
`Runtime::process_incoming_receipts` and `Runtime::process_delayed_receipts` iterate over a list of receipts that is not exclusively controlled by the chunk producer — receipts can be crafted/queued by any account via ordinary transactions, and their fate (arriving as "incoming" vs. sitting "delayed"/buffered) can be influenced by congestion. Both loops call `validate_receipt` on each item and, unlike action execution (where a failing receipt only fails that receipt's own outcome and is rolled back via `state_update.rollback()`), a validation failure here is turned into a hard `RuntimeError` that propagates out of `apply()` entirely. [1](#0-0) 

For delayed receipts, the same failure is explicitly documented as fatal: [2](#0-1) 

This differs from `apply_action_receipt`, where individual action/receipt failures are isolated per-receipt and do not stop the chunk: [3](#0-2) 

### Finding Description
`process_incoming_receipts` and `process_delayed_receipts` are steps of the mandatory `process_receipts` pipeline invoked by every chunk's `apply()`: [4](#0-3) 

Any receipt whose serialized form is present in the incoming set for a shard, or that is sitting in the persistent delayed-receipt queue, is unconditionally revalidated with `validate_receipt(...)`. If that check fails for one receipt, the error is not scoped to that receipt — it is returned as `Err(...)` from `process_incoming_receipts`/`process_delayed_receipts`, which bubbles up through `process_receipts` and ultimately out of `Runtime::apply`, aborting the whole chunk's state transition for that shard (all other unrelated transactions/receipts in the same chunk do not get processed by this call at all).

The nearcore team is aware of exactly this class of issue for receipt *size*: they explicitly patched around receipts that were valid when created but could become problematic under stricter limits later (referencing a real production bug, near/nearcore#12606), by clamping size in the forwarding path so such receipts don't "get stuck": [5](#0-4) 

However, this size-clamping mitigation only covers the outgoing-forwarding/bandwidth-request path (`try_forward`, `generate_bandwidth_request`) — it does not cover the hard `validate_receipt` call executed on dequeue in `process_delayed_receipts` / `process_incoming_receipts`, which still treats *any* validation mismatch (not just size) as fatal.

An attacker-crafted receipt can be legitimately valid at creation time (validated once with `NewReceipt` mode when originally executed) but can be delayed for an extended period via:
- Congestion-based outgoing buffering (`ReceiptSinkV2::buffer_receipt`, drained later by `forward_from_buffer_to_shard`), and
- The delayed-receipt queue on the receiving shard when the gas/compute limit is hit. [6](#0-5) 

If the wasm/receipt validation limits are tightened between the time the receipt was created and validated and the time it is eventually dequeued/delivered (e.g., across a protocol version boundary while the receipt is buffered/delayed — a state that is directly reachable by any account whose receiver shard is congested, or by intentionally saturating a shard's gas limit to push its own receipts into the delayed queue), `validate_receipt` on dequeue can fail, and the failure is fatal to the whole chunk rather than isolated to that one receipt.

### Impact Explanation
A single receipt that becomes invalid relative to current validation rules — despite having been valid and accepted when it was created — causes the entire chunk's `apply()` to return an error instead of completing. Because `apply()` is the deterministic state-transition function every honest validator must execute identically, an unrecoverable `Err` here is a transaction/receipt-triggered halt of chunk processing for that shard: no other transactions or receipts in that chunk are applied, network progress on the shard stalls, and (per the "Delayed receipts must stay valid" invariant documented in the spec) it is explicitly called out as leading to `StorageError::StorageInconsistentState`, i.e., a state treated as a fatal/inconsistent condition rather than a recoverable per-tx failure. This matches the reported bug class: a single "unpriceable"/unvalidatable item in a dynamically processed list denies service to all unrelated participants relying on that critical process (here, an entire shard's chunk application) rather than only the offending item.

### Likelihood Explanation
Reaching this path requires: (1) crafting a receipt near the edge of current validation limits (size/action limits), and (2) causing it to be buffered or delayed rather than executed immediately — both of which are achievable by an ordinary, unprivileged account through normal congestion (a receiver shard being congested, or a shard hitting its gas limit) without needing any special network or operator access. The additional precondition of a validation-tightening protocol upgrade occurring while the receipt is still in flight reduces the likelihood to being timing-dependent, so this is not trivially triggerable at will but is a real latent hazard given the codebase's own documented awareness of exactly this bug class for receipt size (nearcore#12606) that was only partially mitigated (mitigation covers forwarding/size, not the general `validate_receipt` fatal-on-failure path).

### Recommendation
Do not propagate `validate_receipt` failures on dequeue as a whole-chunk-aborting error. Instead:
- For `process_incoming_receipts`/`process_delayed_receipts`, treat a revalidation failure of a single previously-accepted receipt as an isolated failed outcome for that receipt (similar to how `apply_action_receipt` isolates per-receipt failures with `state_update.rollback()`), rather than returning `Err` out of `apply()`.
- Extend the existing size-clamping precedent (`runtime/runtime/src/congestion_control.rs`'s `try_forward`/`generate_bandwidth_request` handling of nearcore#12606) to cover all `validate_receipt` checks performed at dequeue time, so that receipts which were valid at creation cannot be turned into a fatal fault later purely due to subsequent config/protocol changes.

### Proof of Concept
1. Attacker submits a `FunctionCall` action receipt to `receiver_id` R sized just under the current `max_receipt_size` / near current action-count limits, timed so it becomes an outgoing receipt.
2. Attacker (or ambient network conditions) keeps shard(R) congested (e.g., by flooding it with receipts) so the crafted receipt is buffered in `ShardsOutgoingReceiptBuffer` rather than forwarded immediately — see `ReceiptSinkV2::buffer_receipt`/`forward_from_buffer_to_shard`.
3. While the receipt sits buffered/delayed, a protocol upgrade activates that tightens `wasm_config.limit_config` (receipt size/action limits) — a routine, expected occurrence in the network's lifecycle.
4. When the buffered receipt is finally forwarded and becomes an "incoming receipt" on shard(R), `process_incoming_receipts` calls `validate_receipt(..., ExistingReceipt)` on it; it now fails validation under the new limits.
5. The failure is returned via `.map_err(RuntimeError::ReceiptValidationError)?`, aborting `process_incoming_receipts`, `process_receipts`, and ultimately `Runtime::apply` for the entire chunk on shard(R), rather than being isolated to the single offending receipt. [7](#0-6)

### Citations

**File:** runtime/runtime/src/lib.rs (L2628-2640)
```rust
            // Validating the delayed receipt. If it fails, it's likely the state is inconsistent.
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                &receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(|e| {
                StorageError::StorageInconsistentState(format!(
                    "Delayed receipt {:?} in the state is invalid: {}",
                    receipt, e
                ))
            })?;
```

**File:** runtime/runtime/src/lib.rs (L2693-2703)
```rust
        processing_state.outcomes.reserve(processing_state.incoming_receipts.len());
        for receipt in processing_state.incoming_receipts {
            // Validating new incoming no matter whether we have available gas or not. We don't
            // want to store invalid receipts in state as delayed.
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(RuntimeError::ReceiptValidationError)?;
```

**File:** runtime/runtime/src/lib.rs (L2787-2821)
```rust
    fn process_receipts(
        &self,
        processing_state: &mut ApplyProcessingReceiptState,
        receipt_sink: &mut ReceiptSink,
    ) -> Result<ProcessReceiptsResult, RuntimeError> {
        let mut validator_proposals = vec![];
        let apply_state = &processing_state.apply_state;

        // TODO(#8859): Introduce a dedicated `compute_limit` for the chunk.
        // For now compute limit always matches the gas limit.
        let compute_limit = apply_state.gas_limit.map(|g| g.as_gas()).unwrap_or(u64::MAX);

        // We first process local receipts. They contain staking, local contract calls, etc.
        self.process_local_receipts(
            processing_state,
            receipt_sink,
            compute_limit,
            &mut validator_proposals,
        )?;

        // Then we process the delayed receipts. It's a backlog of receipts from the past blocks.
        self.process_delayed_receipts(
            processing_state,
            receipt_sink,
            compute_limit,
            &mut validator_proposals,
        )?;

        // And then we process the new incoming receipts. These are receipts from other shards.
        self.process_incoming_receipts(
            processing_state,
            receipt_sink,
            compute_limit,
            &mut validator_proposals,
        )?;
```

**File:** protocol-model/spec/runtime-execution.md (L151-156)
```markdown
- **Invalid txs make progress, not failure**: a chunk with invalid transactions is not rejected; the offending txs are skipped during conversion, polluting the chain with junk but keeping the shard live (`runtime/runtime/src/lib.rs:1706` doc; skip sites at `:1994`, `:2199`).
- **Refund receipts are free**: system-predecessor receipts burn zero gas; a failed refund burns its deposit into `other_burnt_amount` rather than refunding (`runtime/runtime/src/lib.rs:929`, `:972`).
- **Delayed receipts must stay valid**: a delayed receipt that fails `validate_receipt` on dequeue is treated as `StorageInconsistentState` (`runtime/runtime/src/lib.rs:2500`).
- **No balance-mutation on tx-verify error**: `verify_and_charge_*_ephemeral` are pure; the only historical exception (allowance) is fixed by `FixAccessKeyAllowanceCharging` (v85).
- **Storage staking**: receiver must cover storage after execution or the receipt fails with `LackBalanceForState` (`runtime/runtime/src/lib.rs:897`); zero-balance accounts (≤ `ZERO_BALANCE_ACCOUNT_STORAGE_LIMIT = 770` bytes) are exempt (`runtime/runtime/src/verifier.rs:25`, `:88`).
- **Compute/storage limit**: chunk work bounded by `compute_limit` (= gas limit) and the storage-proof size limit; overflow pushes work to the delayed queue (`runtime/runtime/src/lib.rs:2385`).
```

**File:** runtime/runtime/src/congestion_control.rs (L338-395)
```rust
    fn forward_from_buffer_to_shard(
        &mut self,
        buffer_shard_id: ShardId,
        state_update: &mut TrieUpdate,
        apply_state: &ApplyState,
        shard_layout: &ShardLayout,
    ) -> Result<(), RuntimeError> {
        let mut num_forwarded = 0;
        let mut outgoing_metadatas_updates: Vec<(ByteSize, Gas)> = Vec::new();
        for receipt_result in
            self.outgoing_buffers.to_shard(buffer_shard_id).iter(&state_update.trie, true)
        {
            let receipt = receipt_result?;
            let gas = receipt_congestion_gas(&receipt, &apply_state.config)?;
            let size = receipt_size(&receipt)?;
            let should_update_outgoing_metadatas = receipt.should_update_outgoing_metadatas();
            let receipt = receipt.into_receipt();
            let target_shard_id = receipt.receiver_shard_id(&shard_layout)?;

            match Self::try_forward(
                receipt,
                gas,
                size,
                target_shard_id,
                &mut self.outgoing_limit,
                &mut self.outgoing_receipts,
                apply_state,
                &mut self.stats,
            )? {
                ReceiptForwarding::Forwarded => {
                    self.own_congestion_info.remove_receipt_bytes(size)?;
                    self.own_congestion_info.remove_buffered_receipt_gas(gas.as_gas().into())?;
                    if should_update_outgoing_metadatas {
                        // Can't update metadatas immediately because state_update is borrowed by iterator.
                        outgoing_metadatas_updates.push((ByteSize::b(size), gas));
                    }
                    // count how many to release later to avoid modifying
                    // `state_update` while iterating based on
                    // `state_update.trie`.
                    num_forwarded += 1;
                }
                ReceiptForwarding::NotForwarded(_) => {
                    break;
                }
            }
        }

        self.outgoing_buffers.to_shard(buffer_shard_id).pop_n(state_update, num_forwarded)?;
        for (size, gas) in outgoing_metadatas_updates {
            self.outgoing_metadatas.update_on_receipt_popped(
                buffer_shard_id,
                size,
                gas,
                state_update,
            )?;
        }
        Ok(())
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L412-427)
```rust
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
```
