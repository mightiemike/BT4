### Title
Post-validation mutation of `output_data_receivers` allows outgoing receipts to exceed `max_receipt_size`, bypassing the size-limit check - (File: `runtime/runtime/src/congestion_control.rs`, `runtime/runtime/src/verifier.rs`)

### Summary
This maps to the same bug class as the Dinari report: a restriction mechanism is defined (a "size limit" hook, analogous to the blacklist's `_beforeTokenTransfer`) but is not applied on every code path that actually mutates the protected value, so the restriction can be silently bypassed by an unprivileged contract-call/promise chain.

### Finding Description
`validate_receipt` in `runtime/runtime/src/verifier.rs` enforces `max_receipt_size` only in `ValidateReceiptMode::NewReceipt` mode, computed via `borsh::object_length(receipt)` at the moment the receipt is created [1](#0-0) . This check runs once, right after a `FunctionCall` action produces new receipts, inside `apply_action_receipt` [2](#0-1) .

However, a receipt's `output_data_receivers` field — which grows the serialized size — is populated *after* this validation, when a later promise in the same DAG resolves and attaches itself as an output-data receiver of an earlier promise (`lib.rs:1034`-`1073` per the spec doc, "Output data" step) [3](#0-2) . That mutation is never re-run through `validate_receipt`, so a receipt that was exactly at the limit when first validated can be pushed over `max_receipt_size` afterward.

The nearcore project is aware this exact bypass exists — `try_forward` in the congestion-control forwarding path explicitly documents and works around it rather than fixing the root cause: "There is a bug which allows to create receipts that are above the size limit... Let's pretend that all receipts are at most `max_receipt_size`" (referencing GitHub issue #12606) [4](#0-3) . `ValidateReceiptMode::ExistingReceipt` is documented as deliberately more lenient specifically because of this bug: "There is a bug which allows to create receipts that are above the size limit. Runtime has to handle them gracefully until the receipt size limit bug is fixed" [5](#0-4) .

The test suite reproduces the exact "restriction defined but not enforced on this path" behavior and labels it a known, currently-unfixed bug: [6](#0-5) [7](#0-6) 

### Impact Explanation
An unprivileged contract, reachable by any transaction signer who deploys and calls a contract, can construct a promise DAG (`batch_create`/`promise_then`/`promise_return`) whose intermediate receipt is validated at just under `max_receipt_size`, then have `output_data_receivers` added by a later promise, producing an outgoing/buffered receipt larger than the protocol's hard receipt-size limit. This is one of the exact size caps that state-witness/state-transition invariants are built on (`docs/misc/state_witness_size_limits.md`), so an oversized receipt undermines the ~21 MiB witness-size budget the protocol relies on for chunk validation and cross-shard congestion accounting, and can affect state-root/serialization consistency between honest nodes if size-dependent logic (e.g., `try_forward`'s clamping) diverges from actual on-disk receipt bytes.

### Likelihood Explanation
High reachability: the vulnerable path is triggered purely by a normal `FunctionCall` transaction constructing a promise DAG with `output_data_receivers`, requiring no special privileges, validator status, or network-level access — exactly the "unprivileged transaction signer / contract deployer" scope this scan is meant to catch. The bug is explicitly acknowledged in code comments and covered by dedicated regression tests (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`), confirming it is a live, currently-open issue (#12606) rather than a hypothetical.

### Recommendation
Re-run `validate_receipt` (or at minimum re-check `borsh::object_length(receipt) <= max_receipt_size`) after `output_data_receivers` is mutated for an already-validated receipt, before it is queued as `outgoing_receipts`/`instant_receipts`/buffered. Alternatively, bound the receipt at construction time so `output_data_receivers` cannot later push it past the limit (e.g., reserve worst-case space at initial validation, or split output-data attachment into a separate `Data` receipt whenever it would breach the cap), removing the need for the `try_forward` clamp workaround.

### Proof of Concept
See `test-loop-tests/src/tests/max_receipt_size.rs::test_max_receipt_size_promise_return` and `::test_max_receipt_size_value_return`, which construct a promise DAG (`A -then-> B`, with `A` returning/attaching data that grows the receipt's `output_data_receivers`/data payload after initial size validation) and assert (via `assert_oversized_receipt_occurred`) that the runtime must tolerate a receipt whose serialized size exceeds `max_receipt_size` even though the size check exists and is meant to reject it [8](#0-7) [9](#0-8) .

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

**File:** protocol-model/spec/runtime-execution.md (L67-67)
```markdown
4. **Execute actions in order** (`runtime/runtime/src/lib.rs:848`): for each action compute an `action_hash`, call `apply_action`, and on success validate every newly created receipt with `validate_receipt(..., NewReceipt)` (`:871`). `merge` folds the result; on the first `Err` the loop records the action index and breaks (`runtime/runtime/src/lib.rs:884`).
```

**File:** protocol-model/spec/runtime-execution.md (L73-73)
```markdown
10. **Output data**: if the receipt has `output_data_receivers`, either the returned `ReceiptIndex` receipt absorbs them or `Data` receipts carrying the return value are appended (`runtime/runtime/src/lib.rs:1034`-`1073`).
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-207)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-267)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
/// A[self.return_large_value()] -then-> B[self.mark_test_completed()]
#[test]
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
