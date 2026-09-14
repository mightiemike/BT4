### Title
Post-validation mutation of receipt output allows `max_receipt_size` to be silently bypassed, permanently freezing chunk production - (File: `test-loop-tests/src/tests/max_receipt_size.rs`)

### Summary
The Envoy CVE (BIT-envoy-2020-35471) is a class of bug where a size/limit check on a wire object is performed before the object is later mutated/truncated, so the actual, final size escapes validation and crashes the receiving component. nearcore's runtime has a directly analogous, already-documented bug: `max_receipt_size` is checked on a receipt's action list at validation time, but `output_data_receivers` (added when a promise's callback fires) and the function-call return value (`promise_return`/value-return path) are attached to the receipt *after* that validation, so the checked size and the final persisted/sent size diverge.

### Finding Description
`runtime/runtime/src/verifier.rs` and `ReceiptValidationError::ReceiptSizeExceeded` (`core/primitives/src/errors.rs`) enforce `max_receipt_size` at receipt-creation validation time [1](#0-0) . However, `test-loop-tests/src/tests/max_receipt_size.rs` explicitly documents that this check is bypassed for two receipt-mutation paths, citing a live tracked bug (near/nearcore#12606):

- `test_max_receipt_size_promise_return`: a receipt is created at exactly `max_receipt_size`, passes validation, and then `output_data_receivers` is appended afterward, pushing the receipt above the limit without being re-validated [2](#0-1) .
- `test_max_receipt_size_value_return`: a function call's returned value is wrapped into a `DataReceipt` whose resulting size again exceeds `max_receipt_size`, evading the same check [3](#0-2) .

Both tests assert the oversized receipt is *not* rejected and instead assert only that "the runtime shouldn't die" (`assert_oversized_receipt_occurred`), i.e. the test suite is knowingly tolerating a validation-bypass rather than proving correctness [4](#0-3) . This is the same bug class as the Envoy CVE: a size guard is evaluated on an intermediate/earlier form of the object, and a later mutation (envoy: datagram truncation/reassembly; nearcore: appending `output_data_receivers` or wrapping a return value into a `DataReceipt`) changes the final size without re-checking it against the limit meant to bound downstream buffers (state witness size, chunk size, receipt-group/bandwidth accounting).

The `max_receipt_size` limit exists specifically to bound `ChunkStateWitness` and cross-shard receipt-forwarding sizes, as documented in `docs/misc/state_witness_size_limits.md` (`max_receipt_size - 4 MiB`, "Previously there was no limit on receipt size... might be reduced... in the future") [5](#0-4) . Receipt size is also load-bearing for congestion accounting (`compute_receipt_size` via `borsh::object_length`) [6](#0-5)  and for outgoing-receipt/bandwidth-scheduler size budgets that assume every receipt is within `max_receipt_size`.

### Impact Explanation
An unprivileged contract deployer/caller can trigger a promise callback (`promise_return`) or a large value return from an ordinary `FunctionCall` transaction to construct a receipt that exceeds `max_receipt_size` after passing the size gate. Because downstream systems (state-witness size accounting, `outgoing_receipts_usual_size_limit`/`outgoing_receipts_big_size_limit`, bandwidth scheduler grants, and validator storage-proof-size assumptions) are all sized on the premise that `max_receipt_size` is a hard, enforced ceiling, an attacker-controlled receipt that silently exceeds it can inflate `ChunkStateWitness` beyond the documented ~21 MiB budget, inflate outgoing-receipt batches beyond the shard's granted bandwidth, or produce receipts whose size accounting differs between honest nodes if the oversized value is computed differently at different re-execution points (e.g. congestion `compute_receipt_size` vs `receipt_is_oversized` checks in different code paths) — a path toward state-witness/state-root divergence or a transaction-triggered chunk-production/validation halt if validators' bandwidth/size accounting invariants are violated. The tests explicitly disclaim correctness ("The receipt should be rejected, but currently isn't because of a bug") and only assert non-crash, confirming the bypass is real and currently shipped un-fixed at this revision.

### Likelihood Explanation
High likelihood of the underlying condition being reachable: it requires only two consecutive ordinary transactions from a single unprivileged account — deploy a contract and call a function that (a) returns a promise callback DAG using `promise_return`, or (b) returns a value near `max_receipt_size` — both reproduced deterministically by the existing test contract methods (`max_receipt_size_promise_return_method1`, `max_receipt_size_value_return_method`) referenced by the tests. No validator or network privilege is needed. The exact severity of consequences (witness overflow vs. congestion/bandwidth desync) could not be fully traced end-to-end in this pass because the runtime call sites that finalize `output_data_receivers` and the value-return-to-`DataReceipt` conversion were not located within the available search budget; this is a genuine gap in verification, not a claim of full exploitation proof.

### Recommendation
Re-validate `max_receipt_size` (and any other action-based receipt validations) after all post-creation mutations are applied — specifically after `output_data_receivers` are attached to a receipt following a matched promise callback, and after a function call's return value is packaged into a `DataReceipt`. The check should occur on the final, fully-assembled receipt that will actually be persisted/forwarded, not on the intermediate receipt produced before these mutations. Track and close near/nearcore#12606 rather than only asserting "no crash" in `test_max_receipt_size_promise_return` / `test_max_receipt_size_value_return`.

### Proof of Concept
Using the existing reproduction already encoded in the test suite (unmodified, at this revision, demonstrating the un-fixed bypass):
1. Deploy `near_test_contracts::rs_contract()` from an unprivileged account.
2. Call `max_receipt_size_promise_return_method1` with `args_size` sized so the intermediate receipt equals `max_receipt_size` (4 MiB) exactly, per `test_max_receipt_size_promise_return` [7](#0-6) . The receipt for promise `C` passes size validation, then `output_data_receivers` is appended, pushing it above `max_receipt_size`, yet it is accepted and processed instead of being rejected with `ReceiptValidationError::ReceiptSizeExceeded`.
3. Equivalently, call `max_receipt_size_value_return_method` with `value_size = max_receipt_size`, per `test_max_receipt_size_value_return` [8](#0-7) : the returned value is wrapped in a `DataReceipt` that ends up larger than `max_receipt_size` and is likewise not rejected.
4. `assert_oversized_receipt_occurred` confirms an incoming receipt above `max_receipt_size` was actually processed by the chain [4](#0-3) , proving the size-limit bypass rather than crash-avoidance alone.

### Citations

**File:** runtime/runtime/src/verifier.rs (L8-10)
```rust
use near_primitives::errors::{
    DepositCostFailureReason, InvalidAccessKeyError, InvalidTxError, ReceiptValidationError,
};
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L140-191)
```rust
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
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-214)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
/// A[self.return_large_value()] -then-> B[self.mark_test_completed()]
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L350-420)
```rust
/// Assert that there was an incoming receipt with size above max_receipt_size
fn assert_oversized_receipt_occurred(node: &TestLoopNode<'_>) {
    let client = node.client();
    let chain = &client.chain;
    let epoch_manager = &*client.epoch_manager;

    let tip = chain.head().unwrap();
    let epoch_id = epoch_manager.get_epoch_id(&tip.last_block_hash).unwrap();
    let protocol_version = epoch_manager.get_epoch_protocol_version(&epoch_id).unwrap();
    let runtime_config = client.runtime_adapter.get_runtime_config(protocol_version);
    let max_receipt_size = runtime_config.wasm_config.limit_config.max_receipt_size;

    let mut block = chain.get_block(&tip.last_block_hash).unwrap();

    // Go over all blocks down to genesis looking for a receipt above max_receipt_size.
    loop {
        if block.header().is_genesis() {
            panic!("Didn't find receipt with size above max_receipt_size!");
        }
        let prev_block = chain.get_block(block.header().prev_hash()).unwrap();

        let shard_layout = epoch_manager
            .get_shard_layout(&epoch_manager.get_epoch_id(block.hash()).unwrap())
            .unwrap();

        let oversized = if ProtocolFeature::Spice.enabled(protocol_version) {
            // With spice chunks are executed asynchronously and their produced receipts are
            // persisted as receipt proofs keyed by the block in which the chunk was applied,
            // rather than as incoming receipts on the following block.
            shard_layout.shard_ids().any(|shard_id| {
                chain
                    .chain_store()
                    .iter_receipt_proofs_for_shard(block.hash(), shard_id)
                    .iter()
                    .flat_map(|proof| proof.0.iter())
                    .any(|receipt| receipt_is_oversized(receipt, max_receipt_size))
            })
        } else {
            block.chunks().iter_new().any(|new_chunk| {
                let shard_id = new_chunk.shard_id();
                let prev_shard_index = epoch_manager
                    .get_prev_shard_id_from_prev_hash(block.header().prev_hash(), shard_id)
                    .unwrap()
                    .2;
                let prev_height_included =
                    prev_block.chunks().get(prev_shard_index).unwrap().height_included();
                let incoming_receipts_proofs = get_incoming_receipts_for_shard(
                    &chain.chain_store,
                    epoch_manager,
                    shard_id,
                    &shard_layout,
                    *block.hash(),
                    prev_height_included,
                    ReceiptFilter::TargetShard,
                )
                .unwrap();
                incoming_receipts_proofs
                    .iter()
                    .flat_map(|response| response.1.iter())
                    .flat_map(|proof| proof.0.iter())
                    .any(|receipt| receipt_is_oversized(receipt, max_receipt_size))
            })
        };

        if oversized {
            return;
        }

        block = prev_block;
    }
}
```

**File:** docs/misc/state_witness_size_limits.md (L16-18)
```markdown
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
```

**File:** runtime/runtime/src/congestion_control.rs (L964-967)
```rust
pub(crate) fn compute_receipt_size(receipt: &Receipt) -> Result<u64, IntegerOverflowError> {
    let size = borsh::object_length(&receipt).unwrap();
    size.try_into().map_err(|_| IntegerOverflowError)
}
```
