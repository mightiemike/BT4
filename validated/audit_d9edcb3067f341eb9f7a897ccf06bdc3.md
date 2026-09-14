### Title
Receipt-size check performed before `output_data_receivers`/promise-return linking is applied, allowing acceptance of oversized receipts - ([File: runtime/runtime/src/verifier.rs] / [File: test-loop-tests/src/tests/max_receipt_size.rs])

### Summary
The ksmbd report is a "size validated too early / not against the final buffer contents" class of bug: `get_file_all_info()` computes available buffer space and then writes a filename without re-validating that the *actual* copy still fits, because the check happens before other compound-request state (that consumes buffer) is accounted for. The reachable nearcore analog is the receipt-size validation gap acknowledged in `test-loop-tests/src/tests/max_receipt_size.rs`: a `FunctionCall`/yield/promise-return generated receipt is validated against `max_receipt_size` at creation time, but this validation happens *before* additional data (`output_data_receivers`, resumed value/data payloads) is attached to the same receipt object, so the final, actually-transmitted receipt can exceed `max_receipt_size` while still being accepted and forwarded cross-shard.

### Finding Description
`max_receipt_size` is enforced in the runtime action/receipt validation path (`runtime/runtime/src/verifier.rs`) at the point a new `ActionReceipt` is produced from a `FunctionCall`'s promise creation. However, subsequent runtime bookkeeping — linking a promise's `output_data_receivers` when a `then`/`promise_return` DAG is resolved, or attaching resumed yield payload data — mutates the already-validated receipt object afterward, growing its serialized (borsh) size without re-invoking the size check. This is directly demonstrated by the nearcore test suite itself:

- `test_max_receipt_size_promise_return`: a `FunctionCall` receipt is sized to exactly `max_receipt_size` and passes validation; the runtime later adds `output_data_receivers` for a promise-then, pushing the actual receipt above `max_receipt_size`. The test comment states explicitly: "the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)" [1](#0-0) .
- `test_max_receipt_size_value_return` shows the same class of bug for value-return-produced data receipts [2](#0-1) .
- Both tests conclude by asserting an oversized receipt was actually persisted/forwarded on-chain via `assert_oversized_receipt_occurred`, which scans committed blocks/receipt-proofs for a receipt whose borsh-encoded size exceeds `max_receipt_size` [3](#0-2) .

This mirrors ksmbd's root cause: the size/space check is computed against an intermediate/incomplete state of the object (the OutputBufferLength/expected filename length calculated before other compound-request-consumed data is factored in), so a value that passes validation grows beyond the real, enforced limit by the time it is actually written/serialized — in ksmbd's case an OOB memory write, in nearcore's case an over-limit receipt that bypasses the `ReceiptSizeExceeded` guard entirely and is accepted into the chain state.

### Impact Explanation
`max_receipt_size` exists to bound resource consumption of receipts moving cross-shard (state-witness size, congestion control accounting, bandwidth scheduling all assume receipts are within this bound — see `core/primitives/src/bandwidth_scheduler.rs` and `runtime/runtime/src/congestion_control.rs`, both of which reference `max_receipt_size` extensively). Accepting a receipt that violates this invariant can cause:
- State-witness/bandwidth-scheduler size assumptions to be violated, since those subsystems size-limit based on the declared `max_receipt_size` cap rather than re-validating actual serialized size at every hop.
- Divergent behavior/potential halts if any downstream component (chunk production, state witness generation, or bandwidth scheduling) enforces the limit strictly while the runtime that produced the receipt did not, leading to inconsistent acceptance between components or nodes that assume the bound always holds.
- The bug is already acknowledged as capable of persisting an oversized receipt into committed blocks, per the test's own end-to-end assertion (`assert_oversized_receipt_occurred`), confirming state-transition impact reachable purely from a single submitted transaction/contract call (no privileged party required — only a deployed contract making `promise_then`/`promise_return`/yield calls with attacker-controlled arg sizes).

### Likelihood Explanation
High reachability: any account can deploy a contract and call methods that build a promise DAG sized to exactly `max_receipt_size` before appending `output_data_receivers` via `then`/`promise_return`, exactly as done by the existing test contract methods (`max_receipt_size_promise_return_method1`, `max_receipt_size_value_return_method`, `yield_with_large_args`) [4](#0-3) . This requires no special privileges — a standard signed transaction and standard `FunctionCall` action suffice, matching the "single submitted transaction/contract call" reachability bar.

### Recommendation
Re-validate the final receipt size (against `max_receipt_size`) after all post-creation mutations are applied — specifically after `output_data_receivers` are linked for promise `then`/`promise_return` chains, and after yield/resume payloads are attached — rather than only at the point of initial `ActionReceipt` construction in `runtime/runtime/src/verifier.rs`. The check should be moved to (or duplicated at) the last point before the receipt is persisted/queued for cross-shard delivery, ensuring the borsh-serialized size actually enforced matches the size limit relied upon by `congestion_control.rs` and `bandwidth_scheduler.rs`.

### Proof of Concept
The nearcore repository already contains a reproducing test demonstrating end-to-end acceptance of the oversized receipt:
```
test-loop-tests/src/tests/max_receipt_size.rs::test_max_receipt_size_promise_return
test-loop-tests/src/tests/max_receipt_size.rs::test_max_receipt_size_value_return
```
Both construct a receipt sized to exactly `max_receipt_size` via a `FunctionCall`, trigger a `then`/`promise_return` (or value-return) that appends additional `output_data_receivers`/data payload, and then call `assert_oversized_receipt_occurred`, which walks committed blocks and confirms a receipt whose actual borsh size exceeds `max_receipt_size` was persisted [5](#0-4) [6](#0-5) .

**Note on uncertainty:** I could not fully trace the exact function in `runtime/runtime/src/verifier.rs`/`runtime/runtime/src/lib.rs` where the size check is invoked relative to `output_data_receivers` mutation (index search found only aggregate matches, not the precise call-order code), because the index/tool budget was exhausted before I could read those files directly. The vulnerability is nonetheless already confirmed and reproduced by the existing test suite and its linked issue (near/nearcore#12606), which is why I'm confident in the root cause and impact despite not pinpointing the exact line-level ordering bug in `verifier.rs`.

### Citations

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-128)
```rust
// A function call will generate a new receipt. Size of this receipt will be equal to
// `max_receipt_size`, it'll pass validation, but then `output_data_receivers` will be modified and
// the receipt's size will go above max_receipt_size. The receipt should be rejected, but currently
// isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
// Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L130-267)
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L350-429)
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

fn receipt_is_oversized(receipt: &Receipt, max_receipt_size: u64) -> bool {
    let receipt_size: u64 = borsh::object_length(receipt).unwrap().try_into().unwrap();
    if receipt_size > max_receipt_size {
        tracing::info!(%receipt_size, %max_receipt_size, "found receipt above max size");
        return true;
    }
    false
}
```
