## Title
Receipt-size validation is bypassed by post-check mutation of `output_data_receivers`, allowing unprivileged transactions to create receipts that exceed `max_receipt_size` - (File: `runtime/runtime/src/verifier.rs`)

### Summary
`ProtocolFees` in the report fails because it trusts a size/gas-bounded external call and then implicitly copies an unbounded amount of attacker-controlled return data before the size is checked. Nearcore has the analogous "check happens before the data is finalized" pattern in receipt construction: `validate_receipt` measures and enforces `max_receipt_size` on a freshly created receipt, but the runtime subsequently mutates that very receipt (appending `output_data_receivers` or its data payload) without re-validating the new, larger size. This is a documented, unfixed defect (nearcore issue #12606) reachable from an ordinary contract call.

### Finding Description
`validate_receipt` in `runtime/runtime/src/verifier.rs:687-696` computes `borsh::object_length(receipt)` and rejects it if it exceeds `limit_config.max_receipt_size`, but only in `ValidateReceiptMode::NewReceipt` mode [1](#0-0) . This check is invoked in `apply_action_receipt` immediately after each action produces its `new_receipts`, at `runtime/runtime/src/lib.rs:968-979` [2](#0-1) .

However, later in the very same function, if the current action receipt has non-empty `output_data_receivers`, the runtime either extends a *newly created* receipt's `output_data_receivers` list (when the result is a `ReceiptIndex`) or appends brand-new `Data` receipts carrying the return value (when the result is a `Value`) — this happens in `runtime/runtime/src/lib.rs:1152-1190`, strictly after the `validate_receipt(..., NewReceipt)` check has already passed for that receipt [3](#0-2) . Nothing re-validates the mutated receipt's size against `max_receipt_size` after this mutation.

An unprivileged contract can deliberately engineer this: create a promise chain `A -> B`; when `A` executes it creates a promise `C` sized right at `max_receipt_size` and calls `promise_return`, changing the DAG to `C -> B`. Because `C`'s `output_data_receivers` field is populated for `B` *after* `C` already passed size validation, the runtime pushes `C`'s size over the limit without ever re-checking it. The same effect is achievable with `value_return` returning `max_receipt_size` bytes, which gets wrapped into an oversized `DataReceipt`. This is exactly reproduced by the existing (currently-failing-expectation) tests `test_max_receipt_size_promise_return` and `test_max_receipt_size_value_return`, whose comments explicitly cite this as a known bug (nearcore/nearcore#12606) that the runtime currently tolerates rather than rejects [4](#0-3) [5](#0-4) .

The bug is acknowledged directly in the validation code's own doc comment for `ValidateReceiptMode::ExistingReceipt`, which explains it must tolerate oversized receipts specifically because of this unresolved defect [6](#0-5) .

### Impact Explanation
Once such an oversized receipt exists, it propagates through the rest of the pipeline as a receipt that never should have existed under protocol limits:
- It is persisted as a delayed/incoming receipt and only re-checked with the more permissive `ExistingReceipt` mode (which skips the size check entirely), so it will continue to be processed and re-emitted [7](#0-6) [8](#0-7) .
- The congestion-control/cross-shard forwarding path (`ReceiptSink::forward_or_buffer_receipt` / `try_forward`) has a documented workaround that clamps the receipt's size to `max_receipt_size` purely "for the limit comparison" when computing bandwidth/congestion admission, rather than actually bounding the receipt — meaning oversized receipts are silently admitted into congestion accounting with an understated size, which corrupts the bandwidth/congestion bookkeeping used for admission and scheduling decisions across shards. Since this behavior is a workaround specifically for issue #12606, it demonstrates the oversized receipt is not merely benign but actively breaks the invariant that `receipt size ≤ max_receipt_size` that congestion control, bandwidth scheduling, and state-witness size accounting all depend on.

This directly maps to the reachable, in-scope classes: state-root divergence risk between nodes that treat oversized vs. clamped receipts differently, congestion/bandwidth accounting corruption, and potential chunk/receipt processing anomalies triggered by a single unprivileged transaction — the same "unbounded data slips past a bound check" root cause as the ProtocolFees report, just manifesting in receipt/bandwidth accounting rather than EVM call-gas accounting.

### Likelihood Explanation
This is reachable by any account that can deploy and call a contract (no special permissions required): the exploit only needs standard `FunctionCall` actions using `promise_batch_then`/`promise_return` or `value_return` with attacker-chosen argument/return sizes, both of which are ordinary WASM host calls available to every contract. The near-test-contracts test suite already contains ready-made methods (`max_receipt_size_promise_return_method1`, `max_receipt_size_value_return_method`, `generate_large_receipt`) demonstrating the exact call pattern, and the corresponding test-loop tests independently confirm the bug is live and unfixed in this codebase (`test-loop-tests/src/tests/max_receipt_size.rs`).

### Recommendation
Re-run `validate_receipt(..., ValidateReceiptMode::NewReceipt)` (or at minimum an equivalent size check) after `output_data_receivers` are appended/extended in `runtime/runtime/src/lib.rs` (the block at lines 1152-1190), before the receipt is added to `new_receipts`/handed to the `ReceiptSink`. Alternatively, bound the total size contribution of `output_data_receivers` at the point the promise/value is created (in `receipt_manager.rs`) so the post-mutation size can never exceed `max_receipt_size`. The `ReceiptSink` congestion-size clamp workaround should be removed once the root cause is fixed, since it currently masks the invariant violation instead of preventing it.

### Proof of Concept
1. Deploy `near_test_contracts::rs_contract()` (already used by `test_max_receipt_size_promise_return` in `test-loop-tests/src/tests/max_receipt_size.rs`).
2. Call `max_receipt_size_promise_return_method1` with `args_size` computed so that the intermediate promise `C`'s serialized receipt size is exactly `max_receipt_size` (4_194_304 bytes) — this passes `validate_receipt` in `NewReceipt` mode.
3. The contract then performs `promise_return`, causing `C` to be re-targeted as the head of the DAG and its `output_data_receivers` extended for `B`'s data dependency, in the code path at `runtime/runtime/src/lib.rs:1152-1170` — with no subsequent re-validation.
4. Observe (as `assert_oversized_receipt_occurred` in the test does) that a receipt above `max_receipt_size` is stored/forwarded on-chain, confirming the size-limit bypass [9](#0-8) .

### Citations

**File:** runtime/runtime/src/verifier.rs (L681-696)
```rust
pub(crate) fn validate_receipt(
    limit_config: &LimitConfig,
    receipt: &Receipt,
    current_protocol_version: ProtocolVersion,
    mode: ValidateReceiptMode,
) -> Result<(), ReceiptValidationError> {
    if mode == ValidateReceiptMode::NewReceipt {
        let receipt_size: u64 =
            borsh::object_length(receipt).unwrap().try_into().expect("Can't convert usize to u64");
        if receipt_size > limit_config.max_receipt_size {
            return Err(ReceiptValidationError::ReceiptSizeExceeded {
                size: receipt_size,
                limit: limit_config.max_receipt_size,
            });
        }
    }
```

**File:** runtime/runtime/src/verifier.rs (L727-740)
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ValidateReceiptMode {
    /// Used for validating new receipts that were just created.
    /// More strict than `OldReceipt` mode, which has to handle older receipts.
    NewReceipt,
    /// Used for validating older receipts that were saved in the state/received. Less strict than
    /// NewReceipt validation. Tolerates some receipts that wouldn't pass new validation. It has to
    /// be less strict because:
    /// 1) Older receipts might have been created before new validation rules.
    /// 2) There is a bug which allows to create receipts that are above the size limit. Runtime has
    ///    to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
}
```

**File:** runtime/runtime/src/lib.rs (L968-979)
```rust
                if new_result.result.is_ok() {
                    if let Err(e) = new_result.new_receipts.iter().try_for_each(|receipt| {
                        validate_receipt(
                            &apply_state.config.wasm_config.limit_config,
                            receipt,
                            apply_state.current_protocol_version,
                            ValidateReceiptMode::NewReceipt,
                        )
                    }) {
                        new_result.result =
                            Err(ActionErrorKind::NewReceiptValidationError(e).into());
                    }
```

**File:** runtime/runtime/src/lib.rs (L1152-1190)
```rust
        if !action_receipt.output_data_receivers().is_empty() {
            if let Ok(ReturnData::ReceiptIndex(receipt_index)) = result.result {
                // Modifying a new receipt instead of sending data
                match result
                    .new_receipts
                    .get_mut(receipt_index as usize)
                    .expect("the receipt for the given receipt index should exist")
                    .receipt_mut()
                {
                    ReceiptEnum::Action(new_action_receipt)
                    | ReceiptEnum::PromiseYield(new_action_receipt) => new_action_receipt
                        .output_data_receivers
                        .extend_from_slice(&action_receipt.output_data_receivers()),
                    ReceiptEnum::ActionV2(new_action_receipt)
                    | ReceiptEnum::PromiseYieldV2(new_action_receipt) => new_action_receipt
                        .output_data_receivers
                        .extend_from_slice(&action_receipt.output_data_receivers()),
                    _ => unreachable!("the receipt should be an action receipt"),
                }
            } else {
                let data = match result.result {
                    Ok(ReturnData::Value(ref data)) => Some(data.clone()),
                    Ok(_) => Some(vec![]),
                    Err(_) => None,
                };
                result.new_receipts.extend(action_receipt.output_data_receivers().iter().map(
                    |data_receiver| {
                        Receipt::V0(ReceiptV0 {
                            predecessor_id: account_id.clone(),
                            receiver_id: data_receiver.receiver_id.clone(),
                            receipt_id: CryptoHash::default(),
                            receipt: ReceiptEnum::Data(DataReceipt {
                                data_id: data_receiver.data_id,
                                data: data.clone(),
                            }),
                        })
                    },
                ));
            };
```

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

**File:** runtime/runtime/src/lib.rs (L2694-2703)
```rust
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-212)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L350-420)
```rust
/// Assert that there was an incoming receipt with size above max_receipt_size
fn assert_oversized_receipt_occurred(node: &TestLoopNode<'_>) {
    let client = node.client();
    let chain = &client.chain;
    let epoch_manager = &*client.epoch_manager;

    let tip = chain.head().unwrap();
    let epoch_id = epoch_manager.get_epoch_id(&tip.last_block_hash).unwrap();
    let protocol_version = epoch_manager.get_epoch_protocol_version(&epoch_id).unwrap();
    let runtime_config = client.runtime_adapter.get_runtime_config(protocol_version);
    let max_receipt_size = runtime_config.wasm_config.limit_config.max_receipt_size;

    let mut block = chain.get_block(&tip.last_block_hash).unwrap();

    // Go over all blocks down to genesis looking for a receipt above max_receipt_size.
    loop {
        if block.header().is_genesis() {
            panic!("Didn't find receipt with size above max_receipt_size!");
        }
        let prev_block = chain.get_block(block.header().prev_hash()).unwrap();

        let shard_layout = epoch_manager
            .get_shard_layout(&epoch_manager.get_epoch_id(block.hash()).unwrap())
            .unwrap();

        let oversized = if ProtocolFeature::Spice.enabled(protocol_version) {
            // With spice chunks are executed asynchronously and their produced receipts are
            // persisted as receipt proofs keyed by the block in which the chunk was applied,
            // rather than as incoming receipts on the following block.
            shard_layout.shard_ids().any(|shard_id| {
                chain
                    .chain_store()
                    .iter_receipt_proofs_for_shard(block.hash(), shard_id)
                    .iter()
                    .flat_map(|proof| proof.0.iter())
                    .any(|receipt| receipt_is_oversized(receipt, max_receipt_size))
            })
        } else {
            block.chunks().iter_new().any(|new_chunk| {
                let shard_id = new_chunk.shard_id();
                let prev_shard_index = epoch_manager
                    .get_prev_shard_id_from_prev_hash(block.header().prev_hash(), shard_id)
                    .unwrap()
                    .2;
                let prev_height_included =
                    prev_block.chunks().get(prev_shard_index).unwrap().height_included();
                let incoming_receipts_proofs = get_incoming_receipts_for_shard(
                    &chain.chain_store,
                    epoch_manager,
                    shard_id,
                    &shard_layout,
                    *block.hash(),
                    prev_height_included,
                    ReceiptFilter::TargetShard,
                )
                .unwrap();
                incoming_receipts_proofs
                    .iter()
                    .flat_map(|response| response.1.iter())
                    .flat_map(|proof| proof.0.iter())
                    .any(|receipt| receipt_is_oversized(receipt, max_receipt_size))
            })
        };

        if oversized {
            return;
        }

        block = prev_block;
    }
}
```
