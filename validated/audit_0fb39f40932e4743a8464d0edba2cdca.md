This confirms the exact root cause: `validate_receipt(..., ValidateReceiptMode::NewReceipt)` is called at [1](#0-0)  immediately after each action executes, but the `output_data_receivers` are appended to an already-validated receipt *after* the action loop completes, at [2](#0-1) , with no re-validation of size afterward.

### Title
Receipt size limit bypass via post-validation `output_data_receivers` mutation allows unbounded oversized receipts - (File: `runtime/runtime/src/lib.rs`)

### Summary
`apply_action_receipt` validates every newly created receipt's size against `max_receipt_size` immediately after each action executes via `validate_receipt(..., ValidateReceiptMode::NewReceipt)`. However, after the action loop finishes, the code unconditionally appends the *current* receipt's `output_data_receivers` list onto the last created receipt (when the result is a `ReceiptIndex`) or synthesizes new `Data` receipts carrying the return value — both of which happen *after* the size check has already passed and are never re-validated. This lets a single unprivileged contract call construct a receipt that exceeds `max_receipt_size`, which then propagates through delayed/outgoing receipt queues, the bandwidth scheduler and state witness with an unenforced size bound.

### Finding Description
In `Runtime::apply_action_receipt` (`runtime/runtime/src/lib.rs`), each action's freshly created receipts are size-checked here: [1](#0-0) 

This check calls `validate_receipt` with `ValidateReceiptMode::NewReceipt`, which enforces `receipt_size <= limit_config.max_receipt_size`: [3](#0-2) 

After all actions have executed and this per-action validation has already passed, the runtime mutates the *already-validated* receipts to attach the current receipt's `output_data_receivers`, or creates new `Data` receipts wrapping the return value, entirely outside the validated-and-checked code path: [2](#0-1) 

Two concrete exploitable patterns exist:
1. **`promise_return`/`promise_then` DAG**: a contract creates a receipt `C` whose serialized size is crafted to sit exactly at `max_receipt_size` so it passes the `NewReceipt` check, then does a `promise_return`. The runtime subsequently extends `C.output_data_receivers` with the caller's own `output_data_receivers`, pushing `C` above `max_receipt_size` with no further check — see the reproduction in `test_max_receipt_size_promise_return`: [4](#0-3) 
2. **`value_return`**: a contract calls `value_return` with a value sized up to `max_length_returned_data` (checked independently in the VM host function, not against `max_receipt_size`): [5](#0-4) . The runtime wraps this value into a new `DataReceipt` for every `output_data_receiver`, again without re-checking `max_receipt_size`: [6](#0-5) 

This is a codebase-acknowledged, currently unfixed bug tracked as `near/nearcore#12606`, referenced directly in the validation code's own doc-comment: [7](#0-6) . The receipt-forwarding path (`try_forward` in `congestion_control.rs`) explicitly works around oversized receipts by clamping the *accounting* size (not the actual receipt) to `max_receipt_size` to avoid receipts getting permanently stuck in the outgoing buffer: [8](#0-7) , confirming that the runtime already anticipates oversized receipts reaching this stage in production.

### Impact Explanation
Any transaction signer able to deploy or call a contract that builds a promise DAG or returns a large value can force the runtime to construct and persist a receipt whose serialized size exceeds the protocol-mandated `max_receipt_size` cap. Because `max_receipt_size` is a hard limit meant to bound per-receipt storage-proof/state-witness contribution and outgoing bandwidth accounting, an oversized receipt breaks the invariant relied on by:
- Congestion control's per-receipt size accounting (`compute_receipt_size`/`receipt_congestion_gas`), which assumes receipts never exceed `max_receipt_size`.
- The bandwidth scheduler's outgoing size limits (`outgoing_receipts_usual_size_limit`/`outgoing_receipts_big_size_limit`), which are computed against a receipt-size model that assumes the cap holds; `try_forward`'s clamp is only a stopgap to avoid livelock, not a correctness fix.
- State-witness size limits, since an oversized receipt included in incoming/outgoing receipt proofs can inflate the witness beyond configured bounds.

This constitutes a validated (not just theoretical) protocol-level invariant violation that any external caller can trigger with a single transaction, matching the "invalid state transition acceptance" / unbounded-growth class called out by the CVE analog: the size guard is checked once and then silently bypassed by later unconditional mutation, similar to how the proxygen bug's size check is bypassed once a threshold is crossed, leading to unbounded per-iteration growth.

### Likelihood Explanation
High likelihood of occurrence given the exact reproduction steps are already present as tests in the codebase (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`), explicitly asserting that the oversized-receipt condition currently occurs (`assert_oversized_receipt_occurred`). No special privileges are required — a standard signed transaction deploying and invoking `near-test-contracts`-style methods (`promise_then`/`value_return`) is sufficient.

### Recommendation
Re-validate receipt size with `validate_receipt(..., ValidateReceiptMode::NewReceipt)` (or an equivalent size-only check) *after* `output_data_receivers` are appended and after `Data` receipts are synthesized from `value_return`, immediately before receipts are handed to `result.new_receipts`/`ReceiptSink`. If a receipt exceeds `max_receipt_size` at that point, the enclosing action/receipt should fail with `ReceiptSizeExceeded` rather than being forwarded, closing the gap tracked by `near/nearcore#12606`.

### Proof of Concept
Use the existing in-repo reproduction, e.g. `test_max_receipt_size_value_return` in [9](#0-8) :
1. Deploy `near_test_contracts::rs_contract()`.
2. Call `max_receipt_size_value_return_method` with `{"value_size": 4194304}` (i.e., `max_receipt_size`), which internally calls `return_large_value` via `promise_then`, chained to `mark_test_completed`.
3. The transaction succeeds and the DAG completes, but the resulting `Data` receipt carrying the ~4 MiB return value exceeds `max_receipt_size` (base receipt overhead pushes it over), as confirmed by `assert_oversized_receipt_occurred`, which walks the chain looking for — and finds — an incoming receipt whose borsh-serialized size is above `max_receipt_size`.

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

**File:** runtime/runtime/src/verifier.rs (L687-696)
```rust
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

**File:** runtime/runtime/src/verifier.rs (L732-739)
```rust
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-212)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L216-267)
```rust
fn test_max_receipt_size_value_return() {
    init_test_logger();

    let account = create_account_id("account0");
    let account_signer = create_user_test_signer(&account);
    let mut env = TestLoopBuilder::new()
        .enable_rpc()
        .add_user_account(&account, Balance::from_near(10_000))
        .build();

    // Deploy the test contract
    let deploy_contract_tx = SignedTransaction::deploy_contract(
        101,
        &account,
        near_test_contracts::rs_contract().into(),
        &account_signer,
        env.rpc_node().head().last_block_hash,
    );
    env.rpc_runner().run_tx(deploy_contract_tx, Duration::seconds(5));

    let max_receipt_size = 4_194_304;

    // Call the contract
    let large_receipt_tx = SignedTransaction::call(
        102,
        account.clone(),
        account.clone(),
        &account_signer,
        Balance::ZERO,
        "max_receipt_size_value_return_method".into(),
        format!("{{\"value_size\": {}}}", max_receipt_size).into(),
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
}
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L4578-4585)
```rust
    let num_bytes = return_val.len() as u64;
    if num_bytes > ctx.config.limit_config.max_length_returned_data {
        return Err(HostError::ReturnedValueLengthExceeded {
            length: num_bytes,
            limit: ctx.config.limit_config.max_length_returned_data,
        }
        .into());
    }
```

**File:** runtime/runtime/src/congestion_control.rs (L413-427)
```rust
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
