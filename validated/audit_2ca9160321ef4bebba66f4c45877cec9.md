I found a strong, already-known analog documented in the codebase itself (issue #12606). This precisely matches the CVE-2017-9242 bug class: a size/overflow check performed too early, before data is appended that can push the structure over the checked limit.

### Title
Receipt size validation occurs before `output_data_receivers` is appended, allowing oversized receipts past `max_receipt_size` (analogous to CVE-2017-9242's "check too late/too early relative to append") - ([File: runtime/runtime/src/lib.rs])

### Summary
In `apply_action_receipt`, `validate_receipt(..., NewReceipt)` is invoked immediately after an action produces a new receipt [1](#0-0) , which enforces `ReceiptSizeExceeded` against `limit_config.max_receipt_size` in `validate_receipt` [2](#0-1) . However, after that validation point, the runtime unconditionally appends the *caller's* `output_data_receivers` onto the newly created receipt (`new_action_receipt.output_data_receivers.extend_from_slice(...)`) without re-checking the size limit [3](#0-2) . This is structurally the same defect class as CVE-2017-9242: the size/overwrite check happens before an operation that can grow the structure past the checked bound, rather than after.

### Finding Description
The sequence in `apply_action_receipt` (`runtime/runtime/src/lib.rs`) is:
1. `apply_action` runs a `FunctionCall`, creating new receipts via `receipt_manager` (built to be right at or under `max_receipt_size`).
2. Immediately after action execution, each freshly created receipt is validated with `validate_receipt(&limit_config, ..., ValidateReceiptMode::NewReceipt)`, which computes `borsh::object_length(receipt)` and errors with `ReceiptSizeExceeded` if it is over `max_receipt_size` [1](#0-0) [4](#0-3) .
3. Later in the *same* receipt's processing, if `action_receipt.output_data_receivers()` is non-empty and the function call returned a `ReceiptIndex` (i.e., `promise_return`), the code reaches into `result.new_receipts[receipt_index]` — one of the receipts that was already validated in step 2 — and appends the parent's `output_data_receivers` list onto it via `extend_from_slice`, with no subsequent size check [3](#0-2) .

This lets a contract craft a promise-return DAG (`A` creates `C`, calls `promise_return(C)`, `C` is chained to `B`) where `C`'s receipt is built to sit exactly at `max_receipt_size` so it passes step 2's check, and then the runtime appends `output_data_receivers` in step 3, pushing it over the limit. The oversized receipt is emitted into the outgoing/incoming receipt pipeline before any check catches it. This is a known, acknowledged bug in the repository, tracked as issue #12606 and explicitly called out in comments in `try_forward` and `validate_receipt`'s `ExistingReceipt` mode docstring as something the runtime "has to handle... gracefully until the receipt size limit bug is fixed" [5](#0-4) [6](#0-5) . The `test_max_receipt_size_promise_return` and `test_max_receipt_size_value_return` tests in the repo directly reproduce oversized receipts via this exact code path and only assert that the runtime doesn't crash — they do not assert rejection, because currently there is none [7](#0-6) [8](#0-7) .

### Impact Explanation
An oversized receipt bypasses the `max_receipt_size` protocol invariant that the `ChunkStateWitness` size-limit design depends on (documented budget: `max_receipt_size = 4 MiB` is meant to be a hard cap contributing to the ~21 MiB total witness bound) [9](#0-8) . `try_forward`'s workaround of clamping the *counted* size to `max_receipt_size` while forwarding the *actual* (larger) receipt bytes means the real outgoing/witness payload can silently exceed the accounted budget [6](#0-5) . This can inflate `ChunkStateWitness` beyond intended limits, straining chunk distribution and violating a documented protocol invariant, though the repository's own workaround (clamping counted size) is intended to prevent an outright chunk-production halt or receipt loss for this specific case.

### Likelihood Explanation
Reachable by any account submitting a single transaction that deploys/calls a contract performing a `promise_return` where the intermediate receipt's actions size is deliberately sized to land exactly at `max_receipt_size` before `output_data_receivers` are appended — demonstrated end-to-end in the existing test suite with `near_test_contracts::rs_contract()` and standard `FunctionCall`/`Gas::from_teragas(300)` transactions, no special privileges required [10](#0-9) .

### Recommendation
Re-run `validate_receipt` (or an equivalent size check) after the `output_data_receivers` extension step at `runtime/runtime/src/lib.rs:1152-1170`, and reject/refund the receipt (or fail the action) if it now exceeds `max_receipt_size`, rather than relying on the downstream `try_forward` clamp workaround. Alternatively, account for the worst-case `output_data_receivers` payload size *before* the initial `validate_receipt` call so the size check reflects the receipt's final, post-append size.

### Proof of Concept
The repository already contains a reproducing test, `test_max_receipt_size_promise_return`, which:
1. Deploys `near_test_contracts::rs_contract()`.
2. Builds a promise DAG `A -> (creates C, promise_return(C)) -> C -then-> B`, sizing `C`'s base actions so `borsh::object_length(C) == max_receipt_size` before append.
3. Calls `max_receipt_size_promise_return_method1` with `args_size` computed to hit exactly `max_receipt_size`.
4. Asserts via `assert_oversized_receipt_occurred` that a receipt larger than `max_receipt_size` is found among incoming receipts on-chain, confirming the bypass [11](#0-10) .

### Citations

**File:** runtime/runtime/src/lib.rs (L67-67)
```rust
};
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

**File:** runtime/runtime/src/verifier.rs (L686-696)
```rust
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

**File:** runtime/runtime/src/verifier.rs (L736-739)
```rust
    /// 2) There is a bug which allows to create receipts that are above the size limit. Runtime has
    ///    to handle them gracefully until the receipt size limit bug is fixed.
    ///    See https://github.com/near/nearcore/issues/12606 for details.
    ExistingReceipt,
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L130-208)
```rust
fn test_max_receipt_size_promise_return() {
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
}
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-213)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
```

**File:** docs/misc/state_witness_size_limits.md (L16-18)
```markdown
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
```
