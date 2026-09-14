I have found a directly analogous, already-acknowledged bug in nearcore that matches the exact bug class of the Thumbor report: a value is validated against a size bound at one point in the pipeline (equivalent to the "pre-transform" size clamp), but then subsequently mutated in a later phase without re-validation (equivalent to Thumbor's post-transform `proportion` filter running after `MAX_WIDTH`/`MAX_HEIGHT` clamping), allowing the enforced bound to be silently bypassed.

### Title
Receipt size limit is validated before `output_data_receivers` mutation, allowing bypass of `max_receipt_size` (post-validation growth analogous to Thumbor's post-transform resize) - ([File: runtime/runtime/src/lib.rs])

### Summary
`Runtime::apply_action_receipt` validates every newly created receipt against `max_receipt_size` via `validate_receipt(..., ValidateReceiptMode::NewReceipt)` immediately after `apply_action` returns [1](#0-0) . However, later in the same function, when the action receipt has `output_data_receivers`, the runtime mutates an already-created new receipt in place by appending `action_receipt.output_data_receivers()` to it — this happens *after* the size check already passed [2](#0-1) . This is structurally identical to Thumbor's bug: a bound is enforced at one pipeline stage, then a subsequent transform grows the object past that bound without a follow-up check.

### Finding Description
`validate_receipt` computes `borsh::object_length(receipt)` and rejects it if it exceeds `limit_config.max_receipt_size` [3](#0-2) . This check runs on `new_result.new_receipts` right after `apply_action` executes each action [1](#0-0) .

But when a `FunctionCall` returns `ReturnData::ReceiptIndex(receipt_index)` (i.e. `promise_return`) and the enclosing `action_receipt` has non-empty `output_data_receivers`, the runtime reaches into `result.new_receipts[receipt_index]` — a receipt that already passed the `NewReceipt` size validation — and extends its `output_data_receivers` with the outer receipt's own data receivers [2](#0-1) . This mutation happens unconditionally with no subsequent re-validation against `max_receipt_size`, so a receipt that was exactly at the size limit before this step ends up over it afterward.

The nearcore team has already acknowledged this exact defect (tracked as near/nearcore#12606) and encoded it into `ValidateReceiptMode::ExistingReceipt`, whose doc comment states the mode must tolerate oversized receipts "because... there is a bug which allows to create receipts that are above the size limit" [4](#0-3) . Existing tests (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`) reproduce this and explicitly state the receipt "should be rejected, but currently isn't because of a bug" [5](#0-4) .

### Impact Explanation
An oversized receipt produced this way is not caught at creation time and is only handled defensively downstream (e.g. clamped at the congestion/bandwidth forwarding layer, per `try_forward`'s documented workaround for issue #12606). Because `max_receipt_size` is one of the core invariants used to bound `ChunkStateWitness` size (`max_receipt_size` contributes directly to the ~21 MiB total witness budget documented in `docs/misc/state_witness_size_limits.md`) [6](#0-5) , a receipt that bypasses this bound can inflate witness size beyond what chunk producers/validators expect, and different nodes may treat the oversized receipt inconsistently across validation paths (`NewReceipt` vs. `ExistingReceipt` modes have different tolerance), risking state-witness size assumptions and downstream congestion-control accounting being violated by an attacker-controlled receipt.

### Likelihood Explanation
Reachable by any account submitting a single transaction that calls a deployed contract performing a `promise_create` followed by `promise_return`, with an outer receipt carrying `output_data_receivers` (i.e., simply chaining `.then()` on the promise) and sized close to `max_receipt_size` — exactly what `test_max_receipt_size_promise_return` demonstrates using ordinary `FunctionCall` actions and no special privileges [7](#0-6) .

### Recommendation
Re-run `validate_receipt` (or an equivalent size check) on the mutated receipt in `Runtime::apply_action_receipt` immediately after the `output_data_receivers` are appended at [2](#0-1) , before the receipt is added to `result.new_receipts`/propagated further, and reject or fail the action if the mutated receipt exceeds `max_receipt_size`.

### Proof of Concept
As already captured by nearcore's own regression test: deploy `near_test_contracts::rs_contract()`, then call a method that builds promise DAG `A -then-> B`, where `A` (when executed) creates receipt `C` sized to exactly `max_receipt_size` and calls `promise_return(C)`. Because `A`'s enclosing receipt has `output_data_receivers` (from the `.then()` chain), the runtime appends `A`'s output data receivers into `C` after `C` already passed the `NewReceipt` size check, pushing `C` above `max_receipt_size` without rejection [8](#0-7) .

### Citations

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

**File:** runtime/runtime/src/lib.rs (L1152-1170)
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
```

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

**File:** runtime/runtime/src/verifier.rs (L727-739)
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
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L150-207)
```rust
    // User calls a contract method
    // Contract method creates a DAG with two promises: [A -then-> B]
    // When promise A is executed, it creates a third promise - `C` and does a `promise_return`.
    // The DAG changes to: [C ->then-> B]
    // The receipt for promise C is a maximum size receipt.
    // Adding the `output_data_receivers` to C's receipt makes it go over the size limit.
    let base_receipt_template = Receipt::V0(ReceiptV0 {
        predecessor_id: account.clone(),
        receiver_id: account.clone(),
        receipt_id: CryptoHash::default(),
        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: account.clone(),
            signer_public_key: account_signer.public_key().into(),
            gas_price: Balance::ZERO,
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: vec![Action::FunctionCall(Box::new(FunctionCallAction {
                method_name: "noop".into(),
                args: vec![],
                gas: Gas::ZERO,
                deposit: Balance::ZERO,
            }))],
        }),
    });
    let base_receipt_template = action_receipt_v1_to_latest(&base_receipt_template);
    let base_receipt_size = borsh::object_length(&base_receipt_template).unwrap();
    let max_receipt_size = 4_194_304;
    let args_size = max_receipt_size - base_receipt_size;

    // Call the contract
    let large_receipt_tx = SignedTransaction::call(
        102,
        account.clone(),
        account.clone(),
        &account_signer,
        Balance::ZERO,
        "max_receipt_size_promise_return_method1".into(),
        format!("{{\"args_size\": {}}}", args_size).into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(large_receipt_tx, Duration::seconds(5));

    // Make sure that the last promise in the DAG was called
    let assert_test_completed = SignedTransaction::call(
        103,
        account.clone(),
        account,
        &account_signer,
        Balance::ZERO,
        "assert_test_completed".into(),
        "".into(),
        Gas::from_teragas(300),
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(assert_test_completed, Duration::seconds(5));

    assert_oversized_receipt_occurred(&env.validator());
```

**File:** docs/misc/state_witness_size_limits.md (L16-18)
```markdown
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
```
