### Title
`promise_return` and Value-Return Paths Bypass `max_receipt_size` Invariant, Allowing Unprivileged Contracts to Emit Oversized Receipts That Corrupt Congestion-Control Accounting - (File: `runtime/runtime/src/lib.rs`)

---

### Summary

An unprivileged user can deploy a contract that exploits two code paths in `apply_action_receipt` to emit receipts whose serialized size exceeds the protocol-enforced `max_receipt_size` (4 MiB). The size check runs on newly created receipts before the runtime mutates them; the mutation happens after validation and is never re-checked. The resulting oversized receipts are forwarded cross-shard, where the congestion-control layer silently clamps their reported size to `max_receipt_size`, causing the bandwidth-accounting invariant to be violated. The nearcore codebase explicitly acknowledges this as an open bug (issue #12606) and has added workarounds that tolerate—but do not fix—the invariant break.

---

### Finding Description

**Validation happens before mutation — two paths**

Inside `apply_action_receipt`, after each action executes, every newly created receipt is validated:

```
// runtime/runtime/src/lib.rs:868-877
if new_result.result.is_ok() {
    if let Err(e) = new_result.new_receipts.iter().try_for_each(|receipt| {
        validate_receipt(..., ValidateReceiptMode::NewReceipt)
    }) { ... }
}
result.merge(new_result)?;
``` [1](#0-0) 

`validate_receipt` in `NewReceipt` mode measures the Borsh-serialized size and rejects anything above `max_receipt_size`: [2](#0-1) 

**Path 1 — `promise_return` (`ReturnData::ReceiptIndex`)**

After the per-action validation loop, `apply_action_receipt` checks whether the function call returned a receipt index (`promise_return`). If so, it **mutates** the already-validated receipt by appending the parent receipt's `output_data_receivers` to it:

```rust
// runtime/runtime/src/lib.rs:1040-1047
ReceiptEnum::Action(new_action_receipt) | ... => new_action_receipt
    .output_data_receivers
    .extend_from_slice(&action_receipt.output_data_receivers()),
``` [3](#0-2) 

Each `DataReceiver` entry is ~100 bytes (32-byte `CryptoHash` + up to 64-byte `AccountId`). With `max_number_input_data_dependencies = 128`, the mutation can add up to ~12,800 bytes to a receipt that was already at exactly `max_receipt_size`, producing a receipt of ≈4,207 KB — above the 4,194 KB limit — with no re-validation.

**Path 2 — value return (`ReturnData::Value`)**

When the function call returns a plain value and the parent receipt has `output_data_receivers`, the runtime creates `DataReceipt` wrappers: [4](#0-3) 

`validate_data_receipt` only checks that the payload length does not exceed `max_length_returned_data` (4 MiB): [5](#0-4) 

It does **not** check the total Borsh-serialized receipt size. A 4 MiB payload wrapped in a `DataReceipt` envelope (predecessor/receiver IDs, `receipt_id`, `data_id`, length prefix) produces a receipt of ≈4,194,500 bytes — above `max_receipt_size` — and is forwarded without rejection.

**Congestion-control workaround silently masks the violation**

`try_forward` in `congestion_control.rs` detects oversized receipts and clamps their reported size to `max_receipt_size` before decrementing the bandwidth grant:

```rust
// runtime/runtime/src/congestion_control.rs:413-427
// There is a bug which allows to create receipts that are above the size limit.
if size > max_receipt_size {
    size = max_receipt_size;
}
``` [6](#0-5) 

The grant is decremented by the clamped value, but the actual bytes transmitted are larger. This means the shard's outgoing bandwidth budget is consumed faster than the scheduler accounts for, silently violating the bandwidth invariant.

**`ExistingReceipt` mode skips the size check**

When the oversized receipt arrives at the destination shard, `process_incoming_receipts` validates it with `ExistingReceipt` mode, which explicitly skips the size check: [7](#0-6) [8](#0-7) 

The receipt is therefore executed without error, completing the bypass.

---

### Impact Explanation

The `max_receipt_size` limit exists to bound `ChunkStateWitness` size and to enforce the bandwidth scheduler's per-link size grants. Both invariants are broken:

1. **Bandwidth accounting corruption**: The congestion-control layer underreports the actual bytes forwarded by up to ~12,800 bytes per receipt (path 1) or ~200–500 bytes per receipt (path 2). An attacker who repeatedly triggers path 1 can cause a shard to transmit more data than its bandwidth grant allows, degrading throughput for other users on that link.

2. **Contract execution flow breakage**: The protocol guarantees that no receipt larger than `max_receipt_size` enters the system. This guarantee is broken for any contract that uses `promise_return` with a maximum-size inner receipt or returns a maximum-size value to a callback.

The impact is bounded — the maximum oversize is ~12.8 KB above 4 MiB — but the invariant is definitively broken and the workarounds are acknowledged as temporary.

---

### Likelihood Explanation

Any unprivileged user can deploy a contract (e.g., using `near_test_contracts::rs_contract()`) and call `generate_large_receipt` or `max_receipt_size_promise_return_method1` to trigger the bug. No special permissions, validator access, or key compromise is required. The nearcore test suite itself demonstrates both paths succeed end-to-end. [9](#0-8) [10](#0-9) 

---

### Recommendation

1. **Re-validate after mutation**: After the `promise_return` branch extends `output_data_receivers` (lib.rs:1040–1047), call `validate_receipt(..., NewReceipt)` again on the mutated receipt and propagate a `NewReceiptValidationError` if it now exceeds `max_receipt_size`.

2. **Check total data-receipt size**: In the value-return branch (lib.rs:1056–1068), after constructing each `DataReceipt`, call `validate_receipt(..., NewReceipt)` before pushing it to `new_receipts`. Alternatively, tighten `max_length_returned_data` to `max_receipt_size − envelope_overhead` so the total serialized size can never exceed the limit.

3. **Remove the congestion-control workaround** once the root cause is fixed, so that any future oversized receipt causes a hard failure rather than silent underaccounting.

---

### Proof of Concept

The nearcore test suite already contains a complete, passing end-to-end demonstration:

```
// test-loop-tests/src/tests/max_receipt_size.rs:124-208
// "The receipt should be rejected, but currently isn't because of a bug
//  (See https://github.com/near/nearcore/issues/12606)"
fn test_max_receipt_size_promise_return() { ... }
fn test_max_receipt_size_value_return()   { ... }
```

**Step-by-step for path 1:**

1. Deploy `rs_contract` to `account0`.
2. Call `max_receipt_size_promise_return_method1` with `args_size = max_receipt_size − base_receipt_size`. This creates promise DAG `[A → B]`. When A executes, it creates receipt C (size == `max_receipt_size`) and calls `promise_return(C)`, redirecting B's dependency to C.
3. The runtime validates C at lib.rs:868–877 — passes (size == limit).
4. The runtime then appends B's `output_data_receivers` to C at lib.rs:1040–1047 — no re-validation.
5. C is forwarded cross-shard with actual size > `max_receipt_size`; `try_forward` clamps the reported size to `max_receipt_size` and decrements the grant by the clamped value.
6. The destination shard processes C under `ExistingReceipt` mode (no size check); B's callback executes successfully.
7. `assert_oversized_receipt_occurred` confirms an oversized receipt was observed in the chain. [1](#0-0) [3](#0-2) [6](#0-5) [7](#0-6)

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

**File:** runtime/runtime/src/lib.rs (L1056-1068)
```rust
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

**File:** runtime/runtime/src/verifier.rs (L573-585)
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

**File:** runtime/runtime/src/verifier.rs (L618-631)
```rust
/// Validates given data receipt. Checks validity of the length of the returned data.
fn validate_data_receipt(
    limit_config: &LimitConfig,
    receipt: &DataReceipt,
) -> Result<(), ReceiptValidationError> {
    let data_len = receipt.data.as_ref().map(|data| data.len()).unwrap_or(0);
    if data_len as u64 > limit_config.max_length_returned_data {
        return Err(ReceiptValidationError::ReturnedValueLengthExceeded {
            length: data_len as u64,
            limit: limit_config.max_length_returned_data,
        });
    }
    Ok(())
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
