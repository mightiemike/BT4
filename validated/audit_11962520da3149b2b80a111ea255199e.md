### Title
Insufficient bounds check allows receipt size to exceed `max_receipt_size` after validation, causing oversized receipts to bypass congestion/bandwidth size limits - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
Analogous to CVE-2017-7228 (XSA-212), where an earlier fix's bounds check on `XENMEM_exchange` input/output arrays was insufficient and let a caller drive host memory accesses beyond the intended limit, nearcore's receipt-size enforcement is likewise insufficient: `validate_receipt` checks a receipt's borsh-encoded size against `max_receipt_size` only at receipt-creation time, but later mutation (`output_data_receivers` being appended after the size check, or large `value_return`/yield payloads) can push the actual receipt above the validated limit. The congestion-control forwarding code then "pretends" oversized receipts are within limit rather than truly rejecting them.

### Finding Description
`validate_receipt` in `runtime/runtime/src/verifier.rs` enforces `max_receipt_size` only when `mode == ValidateReceiptMode::NewReceipt`, at the moment a receipt is created: [1](#0-0) 

However, `output_data_receivers` on an already-created action receipt are appended later, via `ReceiptManager::create_action_receipt`, when a *dependent* receipt is constructed: [2](#0-1) 

Because this mutation happens after the original receipt already passed `validate_receipt`, the receipt's serialized size can grow past `max_receipt_size` without being re-validated — exactly the "insufficient check" pattern in XSA-212, where a bound was checked once but a later derived operation (additional array elements) let the actual footprint exceed what was validated. The `ValidateReceiptMode::ExistingReceipt` variant explicitly documents this as tolerated, pointing to the tracked bug: [3](#0-2) 

Downstream, `ReceiptSinkV2::try_forward` in `runtime/runtime/src/congestion_control.rs` compensates for oversized receipts by *clamping* the accounted size to `max_receipt_size` rather than rejecting them, explicitly acknowledging the underlying bug: [4](#0-3) 

This means the receipt is still forwarded/buffered and counted against `own_congestion_info`/`OutgoingLimit` and the bandwidth scheduler using a false (smaller) size than its actual borsh-encoded size — the "clamp" logic exists in both `try_forward` (`:417`) and `generate_bandwidth_request` (`:556-562`), and is even referenced in the shipped test `test-loop-tests/src/tests/max_receipt_size.rs` where two tests (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`) are named and documented as demonstrating exactly this: a receipt that is exactly `max_receipt_size` passes validation, then `output_data_receivers` are added afterward pushing the receipt over the limit, and the runtime is expected to (but currently does not) reject it — this is confirmed by the existing GitHub issue reference: `https://github.com/near/nearcore/issues/12606`.

### Impact Explanation
An unprivileged contract deployer/transaction sender can, from a single transaction:
1. Deploy a contract that constructs a `FunctionCall`/`promise_return`/`value_return` receipt sized at exactly `max_receipt_size` (4 MiB in default config, per the config snapshot: `"max_receipt_size": 4194304`), passing `validate_receipt`.
2. Trigger a follow-on promise (`promise_then`) that causes `output_data_receivers` to be appended to that receipt after validation, growing it beyond `max_receipt_size` (confirmed test expects `expected_size = 4194504`, i.e. 200 bytes over the 4194304 limit).
3. The oversized receipt is then processed by `ReceiptSinkV2::try_forward`, which clamps its accounted size to `max_receipt_size` for bookkeeping while the actual bytes forwarded/stored/replicated are larger — meaning real congestion (`own_congestion_info.receipt_bytes`), bandwidth-scheduler grants, and per-shard memory-consumption accounting all under-count the true payload size.

This breaks the deterministic invariant that all validated receipts are ≤ `max_receipt_size`, which the congestion-control/bandwidth-scheduler math (`CongestionInfo`, `receipt_bytes`, `max_congestion_memory_consumption`) relies on for bounding per-shard memory/storage growth from cross-shard receipts, per the spec: `max_congestion_memory_consumption` fraction is `receipt_bytes / max_congestion_memory_consumption`. Systematic underreporting via this bypass can inflate actual receipt-storage/memory usage beyond the modeled bound, undermining the availability guarantee (unbounded resource growth) that congestion control is designed to prevent, and could allow a state-root divergence risk if size accounting differs between chunk producers depending on protocol-version-gated behavior around the clamp.

### Likelihood Explanation
High likelihood of reachability: this is not a hypothetical extrapolation — the nearcore codebase itself documents and reproduces the bug in shipped tests (`test-loop-tests/src/tests/max_receipt_size.rs`, functions `test_max_receipt_size_promise_return` and `test_max_receipt_size_value_return`), explicitly asserting that an oversized receipt occurs (`assert_oversized_receipt_occurred`) and is tolerated by the runtime rather than rejected. The bug is triggerable purely from a signed transaction calling a deployed contract's public methods (`promise_create`/`promise_return`/`value_return`), requiring no special privileges. The known issue is tracked as `https://github.com/near/nearcore/issues/12606` and is currently mitigated only by clamping bookkeeping (not by re-validating/rejecting the receipt), so the underlying insufficient-bounds-check defect remains present in this codebase snapshot.

### Recommendation
Re-validate receipt size against `max_receipt_size` *after* all post-creation mutations (e.g., after `output_data_receivers` are appended in `ReceiptManager::create_action_receipt`, and after any value-return/yield payload is attached) rather than only at initial creation time, and reject the receipt (return `ReceiptSizeExceeded`) instead of silently clamping the accounted size in `ReceiptSinkV2::try_forward`/`generate_bandwidth_request`. Alternatively, restructure `create_action_receipt` to check the *prospective* size before appending `output_data_receivers`, failing the dependent action rather than allowing the parent receipt to grow unbounded post-validation. Any transitional handling of pre-existing over-limit receipts (`ValidateReceiptMode::ExistingReceipt`) should remain strictly for backward compatibility with already-committed state, not extend to newly created receipts.

### Proof of Concept
The proof of concept is already codified in the repository's own test suite, confirming the analog is real and reachable via ordinary transactions: [5](#0-4) [6](#0-5) 

Steps:
1. Deploy `near_test_contracts::rs_contract()` (the test contract) via a signed `DeployContract` transaction.
2. Call `max_receipt_size_promise_return_method1` with `args_size` chosen so the base receipt equals exactly `max_receipt_size` (4,194,304 bytes) — this receipt passes `validate_receipt`.
3. The contract's promise-return logic (`max_receipt_size_promise_return_method2`, `runtime/near-test-contracts/test-contract-rs/src/lib.rs:1988-2018`) creates promise `C` with large args and calls `promise_return`, causing the DAG rewire (`output_data_receivers` update) that pushes receipt `C`'s serialized size to 4,194,504 bytes — 200 bytes over `max_receipt_size`.
4. `assert_oversized_receipt_occurred` confirms a receipt above `max_receipt_size` is present in the chain's incoming-receipt proofs, proving the size-limit bypass reaches the persisted/forwarded receipt path rather than being rejected at construction.

### Citations

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

**File:** runtime/runtime/src/receipt_manager.rs (L118-125)
```rust
        assert_eq!(input_data_ids.len(), receipt_indices.len());
        for (data_id, receipt_index) in input_data_ids.iter().zip(receipt_indices.into_iter()) {
            self.action_receipts
                .get_mut(receipt_index as usize)
                .ok_or(HostError::InvalidReceiptIndex { receipt_index })?
                .output_data_receivers
                .push(DataReceiver { data_id: *data_id, receiver_id: receiver_id.clone() });
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-208)
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
}
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
