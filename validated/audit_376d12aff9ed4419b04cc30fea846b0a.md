This nearcore codebase already contains a documented instance of exactly this bug class — a size limit that is checked once and then silently violated after the check, because more data is appended to the buffer/structure afterward, exactly mirroring the `davinci_mmc` pattern of "no check on transmitted data size against the buffer's real length."

### Title
Receipt size validation is checked before `output_data_receivers` is appended, allowing acceptance of receipts that exceed `max_receipt_size` - ([File: runtime/runtime/src/lib.rs])

### Summary
`validate_receipt()` in `runtime/runtime/src/verifier.rs` enforces the `max_receipt_size` hard limit by Borsh-serializing a freshly created receipt and rejecting it if the size exceeds the limit [1](#0-0) . However, this check runs on the receipt as produced by `receipt_manager::create_action_receipt`, which always starts with an empty `output_data_receivers: vec![]` [2](#0-1) . After the newly created receipts pass through `validate_receipt`, the runtime execution loop in `process_action_receipt` (Runtime::apply_action_receipt path) mutates an already-validated new receipt by appending the *caller's* `output_data_receivers` into it via `extend_from_slice`, growing the receipt's serialized size without re-validating it: [3](#0-2) .

### Finding Description
The size check is analogous to the `davinci_mmc` bug: a length/size limit (`sgm->length` in the kernel bug, `max_receipt_size` here) is enforced against the wrong point-in-time snapshot of the data, then more data is copied/appended in afterwards without re-checking the bound. This is a known, currently unfixed issue tracked as nearcore issue #12606, and the codebase explicitly documents and works around it rather than fixing the root cause:
- `ValidateReceiptMode::ExistingReceipt` exists specifically "to handle them gracefully until the receipt size limit bug is fixed" [4](#0-3) .
- `ReceiptSink::forward_or_buffer_receipt`/`try_forward` in congestion control has to clamp the receipt's size to `max_receipt_size` for its own accounting "bug workaround for oversized receipts, issue #12606" .
- A dedicated regression test acknowledges the runtime accepts and must not crash on oversized receipts produced this way: `test_max_receipt_size_promise_return` / `test_max_receipt_size_value_return` [5](#0-4) .

The attack path is reachable from an ordinary contract call chain (`promise_create` → `promise_then` → `promise_return`) that is sized so the intermediate receipt is right at `max_receipt_size` before `output_data_receivers` is attached, and a caller with pending output data receivers on it, causing the extension in `lib.rs` to push it over the hard limit post-validation.

### Impact Explanation
This lets an unprivileged contract deployer/caller construct receipts whose true Borsh-serialized size permanently exceeds the protocol's `max_receipt_size` hard limit, which is specifically designed to bound `ChunkStateWitness` size and to keep receipts within the stateless-validation storage/size budget (`docs/misc/state_witness_size_limits.md`). Because the bound is a hard, protocol-relevant invariant and the check is bypassable, this is an accepted invalid state transition: receipts that should be rejected are persisted into state and propagated cross-shard, undermining the size guarantees the entire state-witness/stateless-validation design (and the `outgoing_receipts_*_size_limit`/congestion accounting) rely on.

### Likelihood Explanation
High likelihood of reachability — no privilege beyond deploying/calling a contract is required, and the exact reproduction steps are already codified as test cases in the repository (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`), confirming the bug is deterministically triggerable via a normal transaction.

### Recommendation
Re-validate (or re-size-check) a receipt's `output_data_receivers` extension step in `Runtime::apply_action_receipt` (the `lib.rs:1152-1170` code path) against `max_receipt_size` before committing the mutated receipt, or perform the `validate_receipt` size check after all mutations (including `output_data_receivers` extension) are finalized rather than only at creation time in `receipt_manager`.

### Proof of Concept
1. Deploy `near_test_contracts::rs_contract()`.
2. Call `max_receipt_size_promise_return_method1`, which creates promise DAG `A -> B` then, from within `A`, creates promise `C` sized so that `C`'s serialized receipt is exactly `max_receipt_size` before any output data receivers, and does `promise_return(C)` [6](#0-5) .
3. Because of `promise_return`, the runtime rewires the DAG to `C -> B`, appending `B`'s `output_data_receivers` onto the already-size-validated `C` receipt via `extend_from_slice` [3](#0-2) , pushing `C` above `max_receipt_size` without any re-validation.
4. Observed in `test-loop-tests`: the oversized receipt is accepted into the runtime instead of being rejected with `ReceiptSizeExceeded` [7](#0-6) .

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

**File:** runtime/runtime/src/receipt_manager.rs (L112-138)
```rust
    pub(super) fn create_action_receipt(
        &mut self,
        input_data_ids: Vec<CryptoHash>,
        receipt_indices: Vec<ReceiptIndex>,
        receiver_id: AccountId,
    ) -> Result<ReceiptIndex, VMLogicError> {
        assert_eq!(input_data_ids.len(), receipt_indices.len());
        for (data_id, receipt_index) in input_data_ids.iter().zip(receipt_indices.into_iter()) {
            self.action_receipts
                .get_mut(receipt_index as usize)
                .ok_or(HostError::InvalidReceiptIndex { receipt_index })?
                .output_data_receivers
                .push(DataReceiver { data_id: *data_id, receiver_id: receiver_id.clone() });
        }

        let new_receipt = ActionReceiptMetadata {
            receiver_id,
            refund_to: None,
            output_data_receivers: vec![],
            input_data_ids,
            actions: vec![],
            is_promise_yield: false,
        };
        let new_receipt_index = self.action_receipts.len() as ReceiptIndex;
        self.action_receipts.push(new_receipt);
        Ok(new_receipt_index)
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-209)
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

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L1988-2018)
```rust
/// Do a promise_return with a large receipt.
/// The receipt has a single FunctionCall action with large args.
/// Creates DAG:
/// C[self.noop(large_args)] -then-> B[self.mark_test_completed()]
#[no_mangle]
pub unsafe fn max_receipt_size_promise_return_method2() {
    input(0);
    let mut args = vec![0u8; register_len(0) as usize];
    read_register(0, args.as_mut_ptr());
    let input_args_json: serde_json::Value = serde_json::from_slice(&args).unwrap();
    let args_size = input_args_json["args_size"].as_u64().unwrap();

    current_account_id(0);
    let current_account = vec![0u8; register_len(0) as usize];
    read_register(0, current_account.as_ptr() as _);

    let large_args = vec![0u8; args_size as usize];
    let noop_method = b"noop";
    let promise_c = promise_create(
        current_account.len() as u64,
        current_account.as_ptr() as u64,
        noop_method.len() as u64,
        noop_method.as_ptr() as u64,
        large_args.len() as u64,
        large_args.as_ptr() as u64,
        0,
        20 * TGAS,
    );

    promise_return(promise_c);
}
```
