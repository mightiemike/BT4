This is exactly the analog needed. This is a real, currently-present (not yet fixed) issue: `ValidateReceiptMode::ExistingReceipt` is a deliberately "legacy/lenient" validation path that skips the `NewReceipt`-only size-limit re-check, and the size-limit tolerance is explicitly a known bug (nearcore issue #12606) that is still live in this codebase — the exact "legacy path option bypasses a directory/size guard" pattern from the CVE.

### Title
Receipt Size-Limit Guard Bypass via Legacy `output_data_receivers` Mutation After `NewReceipt` Validation - (File: `runtime/runtime/src/lib.rs`)

### Summary
`validate_receipt`/`validate_action_receipt` only enforce `max_receipt_size` when called in `ValidateReceiptMode::NewReceipt` mode [1](#0-0) . A receipt created inside `apply_action_receipt` is validated with `NewReceipt` mode immediately after creation, but its `output_data_receivers` are appended *after* that validation call, so its final serialized size can exceed `max_receipt_size` without ever being re-checked [2](#0-1) . Once such an oversized receipt exists, every subsequent path that re-validates it (incoming/delayed receipt processing) intentionally uses the more permissive `ValidateReceiptMode::ExistingReceipt`, which is documented as tolerating exactly this bug ("legacy" acceptance path) rather than rejecting it [3](#0-2) . This is publicly tracked as nearcore issue #12606 and is still present/un-fixed in this codebase, with a dedicated workaround in congestion control that clamps oversized receipts to `max_receipt_size` for forwarding/bandwidth accounting purposes only, not for correctness of state [4](#0-3) .

### Finding Description
The root cause is a TOCTOU-style guard gap between two receipt-validation call sites:

1. `apply_action_receipt` executes each action via `apply_action`, then validates only the receipts returned by that single action with `validate_receipt(..., ValidateReceiptMode::NewReceipt)` [2](#0-1) . `NewReceipt` mode is the only mode that checks `receipt_size > limit_config.max_receipt_size` [1](#0-0) .
2. After this per-action validation, the surrounding receipt-processing logic in `apply_action_receipt` mutates `output_data_receivers` on the receipt (attaching promise return-value routing produced by later/other actions in the same DAG), which is exactly what test `test_max_receipt_size_promise_return` demonstrates growing the receipt above `max_receipt_size` post-validation [5](#0-4) .
3. The resulting oversized receipt is pushed to `ReceiptSink` and travels the pipeline as an ordinary receipt. Every later stage that re-validates it — `process_incoming_receipts` and `process_delayed_receipts` — deliberately calls `validate_receipt` with `ValidateReceiptMode::ExistingReceipt`, whose comment states it is "less strict" precisely "because... there is a bug which allows to create receipts that are above the size limit. Runtime has to handle them gracefully until the receipt size limit bug is fixed" [6](#0-5) [7](#0-6) .
4. `try_forward` in congestion control independently documents this bug and works around it by lying about the receipt's size (clamping to `max_receipt_size`) so that forwarding/bandwidth accounting doesn't get stuck, rather than rejecting the receipt [4](#0-3) .

This precisely mirrors the CVE bug class: a "legacy"/permissive validation mode (`ExistingReceipt`) is used for receipts that should be re-validated as if newly created, and that legacy mode does not consistently apply the same size guard that `NewReceipt` mode applies, letting a receipt that never should have existed flow unchecked through the cross-shard receipt pipeline.

### Impact Explanation
An oversized receipt bypasses the `max_receipt_size` guard that exists specifically to bound the memory/bandwidth/storage cost of any single receipt in the system (state-witness limits, bandwidth scheduler, chunk application costs are all built assuming this bound holds). Because the size is never re-enforced once the receipt exists, and downstream code (bandwidth scheduling, delayed-receipt storage, state witnesses) budgets space and gas based on a receipt being at most `max_receipt_size`, an attacker who can trigger this pattern (any unprivileged function-call contract that fans out promises and attaches `output_data_receivers`, as shown in the PoC test) can create receipts that violate a protocol-wide invariant. Depending on how the inflated receipt is later serialized/counted against state-witness/proof budgets, this is a systemic invariant violation reachable from a single contract call, not merely a "resource" nuisance — it undermines the guarantee that all in-flight receipts respect `max_receipt_size`, which other safety-critical accounting (congestion control, bandwidth requests, state witness size limits) depends on.

### Likelihood Explanation
High likelihood of reachability: the exact reproduction is already checked into the test suite (`test_max_receipt_size_promise_return`), confirming any account can deploy a contract and, with a single subsequent `FunctionCall` transaction that creates a promise DAG (`A -then-> B`, where `A` creates `C` and does `promise_return`), cause `C`'s receipt to exceed `max_receipt_size` after `NewReceipt` validation has already passed [8](#0-7) . No validator, operator, or special privilege is required — only a deployed contract and two ordinary signed transactions from a regular account.

### Recommendation
Re-validate (including the size check) the final receipt shape — after all mutations such as `output_data_receivers` attachment — before it is committed/pushed to the `ReceiptSink`, rather than validating only the pre-mutation receipt with `NewReceipt` mode. Alternatively, move the `output_data_receivers` attachment to occur before the `validate_receipt(..., NewReceipt)` call so the size actually validated matches the size that is persisted and forwarded. `ValidateReceiptMode::ExistingReceipt` should not need to silently tolerate oversized receipts once the true root cause (mutation-after-validation ordering) is fixed; the size-limit exception it carries for issue #12606 should be removed once this ordering fix lands.

### Proof of Concept
The existing test in the codebase is a working PoC: `test_max_receipt_size_promise_return` in `test-loop-tests/src/tests/max_receipt_size.rs` deploys a contract, then submits a `FunctionCall` transaction (`max_receipt_size_promise_return_method1`) whose promise chain constructs promise `C` with a computed `args_size` so that `C`'s receipt is exactly `max_receipt_size` at creation/validation time; the runtime then attaches `output_data_receivers` to `C`, pushing it over the limit, and the test explicitly asserts `assert_oversized_receipt_occurred` rather than a validation rejection [9](#0-8) .

### Citations

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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L129-207)
```rust
#[test]
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
```
