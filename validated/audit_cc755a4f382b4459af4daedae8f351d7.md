### Title
Receipt Size Limit Bypassed via `promise_return` Mutation After Validation — (`runtime/runtime/src/lib.rs`)

### Summary

The nearcore runtime validates a newly-created action receipt's size against `max_receipt_size` (4 MiB) immediately after each action executes. However, when a contract uses `promise_return`, the runtime subsequently **mutates** the returned receipt by appending `output_data_receivers` from the parent receipt — after validation has already passed. The mutated receipt is never re-validated, so it enters the cross-shard forwarding pipeline above the size limit. The receiving shard processes it under `ValidateReceiptMode::ExistingReceipt`, which explicitly skips the size check. The codebase acknowledges this as a live bug (issue #12606) and has a workaround in the congestion-control layer, but the root cause — the missing post-mutation re-validation — remains unfixed.

### Finding Description

**Step 1 — Per-action validation (passes).**

In `apply_action_receipt`, after each action executes, every newly-created receipt is validated in `NewReceipt` mode: [1](#0-0) 

`validate_receipt` in `NewReceipt` mode checks the borsh-serialized size against `limit_config.max_receipt_size`: [2](#0-1) 

At this point, receipt C (the `promise_return` target) is exactly at or just below `max_receipt_size`, so validation passes.

**Step 2 — Post-validation mutation (the bug).**

After all per-action validations complete, `apply_action_receipt` handles `promise_return` by appending the parent receipt's `output_data_receivers` directly into receipt C's field: [3](#0-2) 

No size re-check occurs after this mutation. Receipt C now exceeds `max_receipt_size`.

**Step 3 — Receiving shard skips the size check.**

When the oversized receipt arrives at the receiving shard, `process_incoming_receipts` validates it with `ValidateReceiptMode::ExistingReceipt`: [4](#0-3) 

`ExistingReceipt` mode intentionally skips the size check: [5](#0-4) 

The comment in the source explicitly names issue #12606 as the reason for this leniency.

**Step 4 — Congestion-control workaround (not a fix).**

The only mitigation is in `try_forward`, which clamps the oversized receipt's size to `max_receipt_size` for bandwidth-limit accounting purposes so it does not get permanently stuck: [6](#0-5) 

This does not prevent the receipt from being forwarded and executed; it only prevents it from blocking the outgoing buffer forever.

**Confirmed by existing tests.**

The test suite explicitly demonstrates that oversized receipts do reach the receiving shard and get processed: [7](#0-6) [8](#0-7) 

### Impact Explanation

An unprivileged user deploys a contract that crafts a receipt C sized at exactly `max_receipt_size - sizeof(DataReceiver)`, then calls `promise_return` inside a promise chain. The runtime appends one `DataReceiver` entry, pushing C above the limit. The receipt is forwarded cross-shard and executed without rejection. This breaks the receipt-size invariant: a receipt that the protocol requires to be rejected is instead executed. Additionally, the state witness for the receiving shard's chunk will include the oversized receipt, causing the witness to exceed the intended 17 MiB ceiling documented in `docs/misc/state_witness_size_limits.md`. [9](#0-8) 

### Likelihood Explanation

Any user who can deploy a contract (standard mainnet capability) can trigger this. The contract only needs to: (1) create a promise chain `A.then(B)`, (2) inside A's execution call `promise_return(C)` where C's receipt is sized to exactly `max_receipt_size` minus the borsh size of one `DataReceiver`. The test contract `max_receipt_size_promise_return_method1` in `near_test_contracts` already demonstrates the exact construction. [10](#0-9) 

### Recommendation

After the `output_data_receivers` extension at `lib.rs:1040-1047`, re-run `validate_receipt` in `NewReceipt` mode on the mutated receipt. If the size now exceeds `max_receipt_size`, treat it as a `NewReceiptValidationError` and fail the action (returning the gas/deposit to the signer), consistent with how other oversized receipts are handled. The same re-validation should be applied to the `ActionV2`/`PromiseYieldV2` arms at lines 1044-1047. [11](#0-10) 

### Proof of Concept

1. Deploy `near_test_contracts::rs_contract()` to `account0`.
2. Compute `args_size = max_receipt_size - base_receipt_size` (as done in the existing test).
3. Call `max_receipt_size_promise_return_method1` with `{"args_size": <computed>}`.
4. Observe that `assert_oversized_receipt_occurred` passes — confirming an oversized receipt was forwarded and processed on-chain.

The existing test `test_max_receipt_size_promise_return` in `test-loop-tests/src/tests/max_receipt_size.rs` is a complete, runnable proof of concept that the nearcore team has already confirmed produces an oversized receipt in the live execution path. [12](#0-11)

### Citations

**File:** runtime/runtime/src/lib.rs (L867-878)
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
                    new_result.result = Err(ActionErrorKind::NewReceiptValidationError(e).into());
                }
            }
```

**File:** runtime/runtime/src/lib.rs (L1031-1049)
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

**File:** runtime/runtime/src/lib.rs (L2524-2530)
```rust
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(RuntimeError::ReceiptValidationError)?;
```

**File:** runtime/runtime/src/verifier.rs (L533-542)
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

**File:** runtime/runtime/src/verifier.rs (L573-586)
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L129-208)
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
}
```

**File:** docs/misc/state_witness_size_limits.md (L1-18)
```markdown
## State witness size limits

Some limits were introduced to keep the size of `ChunkStateWitness` reasonable.
`ChunkStateWitness` contains all the incoming transactions and receipts that will be processed during chunk application and in theory a single receipt could be tens of megabytes in size. Distributing a `ChunkStateWitness` this large would be troublesome, so we limit the size and number of transactions, receipts, etc. The limits aim to keep the total uncompressed size of `ChunkStateWitness` under 17MiB.

There are two types of size limits:

* Hard limit - the size must be below this limit, anything else is considered invalid
* Soft limit - things are added until the limit is exceeded, after that things stop being added. The last added thing is allowed to slightly exceed the limit.

The limits are:

* `max_transaction_size = 1.5 MiB`
  * All transactions must be below 1.5 MiB, otherwise they'll be considered invalid and rejected.
  * Previously was 4MiB, now reduced to 1.5MiB
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
```
