## Title
Receipt size-limit bypass via post-validation mutation of `output_data_receivers` / returned value allows unbounded oversized receipt creation — (File: `runtime/runtime/src/lib.rs`)

## Summary
Analogous to CVE-2023-34396 (Apache Struts accepting normal multipart form fields with no size sanity check before allocation), nearcore validates a newly created receipt's size against `max_receipt_size` at creation time, but then mutates the receipt afterward — appending `output_data_receivers` or filling in the returned value — without re-checking the size limit. An unprivileged contract call can craft a promise chain (`.then()` / value return) so the receipt is exactly at `max_receipt_size` when checked, then grows past the limit once the runtime attaches output data receivers or the returned data, producing a receipt that exceeds the configured "sanity" bound.

## Finding Description
`runtime/runtime/src/lib.rs` builds a function call's output receipt and, when `output_data_receivers()` is non-empty, later extends the *newly created* receipt's `output_data_receivers` field (or attaches the return value/data payload) after the receipt has already passed its size validation: [1](#0-0) 

This is exactly the bug pattern the codebase's own test suite documents as a known, currently-reproducible issue (tracked as nearcore#12606), reachable purely through unprivileged contract calls that create promise DAGs: [2](#0-1) [3](#0-2) 

The tests construct a receipt that is exactly at `max_receipt_size` (4 MiB) at the point where the size check runs, then trigger `promise_return`/large value return so the post-mutation receipt exceeds the limit: [4](#0-3) 

The assertion helper explicitly walks the chain looking for an incoming receipt whose borsh-serialized size is *above* `max_receipt_size`, confirming the oversized receipt is actually persisted/gossiped rather than being rejected: [5](#0-4) 

By contrast, `max_receipt_size` is meant to be an authoritative sanity limit enforced everywhere a receipt is admitted (state-witness limits, bandwidth scheduler sizing, congestion accounting all assume receipts never exceed it): [6](#0-5) 

This is the same bug class as the Struts CVE: a field (`output_data_receivers`/return value) that is appended to a structure *after* the size sanity check runs, with no re-validation, letting the final serialized/allocated object exceed the intended cap — an unprivileged, transaction-reachable "no sanity limit on a normal field" defect.

## Impact Explanation
An unprivileged account can submit a transaction invoking a contract that creates a promise chain sized to just fit `max_receipt_size` at receipt-creation time, then attaches output data receivers or a large returned value, producing receipts that exceed `max_receipt_size` on-chain. Because every subsystem that reasons about receipt size (bandwidth scheduler allowances, congestion-control byte accounting, chunk/state-witness size limits, cross-shard receipt proofs) assumes `max_receipt_size` is an upper bound, oversized receipts undermine those invariants and can be used to inflate per-chunk/per-shard byte budgets beyond what honest nodes provisioned for, a resource-exhaustion vector consistent with a transaction-triggered DoS. The test file itself notes "Runtime shouldn't die when it encounters a receipt with size above `max_receipt_size`" — i.e., this is treated as at minimum a stability/resource concern, not merely cosmetic.

## Likelihood Explanation
High reachability: this requires only a signed transaction from any account calling a deployed WASM contract that builds a specific promise DAG shape (available today via the test contract used in `test-loop-tests`). No validator privilege, no malicious peer, no special protocol feature is needed — the existing, currently-passing-to-reproduce test cases in the repository (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`) demonstrate the exact transaction sequence that triggers it.

## Recommendation
Re-validate the receipt's serialized size against `max_receipt_size` (or an equivalent length-based check) *after* `output_data_receivers` are appended and after the return-value/data payload is attached in `runtime/runtime/src/lib.rs`, rejecting the action (or truncating/failing the receipt) if the final size exceeds the configured limit, rather than only checking at initial receipt construction.

## Proof of Concept
Reference the repository's own reproduction, already present and unresolved:
- `test_max_receipt_size_promise_return` builds a promise DAG `A -then-> B` where `A` creates promise `C` sized to exactly `max_receipt_size` and calls `promise_return`; the runtime subsequently appends `output_data_receivers` to `C`'s receipt, pushing it over the limit [7](#0-6) 
- `test_max_receipt_size_value_return` has a contract return a value sized at `max_receipt_size`, which is wrapped into a data receipt whose final size then exceeds `max_receipt_size` [8](#0-7) 
- Both call `assert_oversized_receipt_occurred`, which scans on-chain incoming receipts for one whose borsh size exceeds `max_receipt_size`, confirming acceptance of the oversized receipt [9](#0-8) 

Note: I could not fully trace every call site in `congestion_control.rs`/`verifier.rs` that performs the initial `max_receipt_size` check (only `grep` hit counts were available, not the exact validation function body) due to iteration limits, so I cannot state with certainty whether a partial re-check already exists elsewhere that only covers some receipt-mutation paths; the repository's own test comments and the linked upstream issue (#12606) indicate the gap is real and unresolved as of this snapshot.

### Citations

**File:** runtime/runtime/src/lib.rs (L1152-1190)
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
            } else {
                let data = match result.result {
                    Ok(ReturnData::Value(ref data)) => Some(data.clone()),
                    Ok(_) => Some(vec![]),
                    Err(_) => None,
                };
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L150-207)
```rust
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L210-213)
```rust
/// Return a value that is as large as max_receipt_size. The value will be wrapped in a data receipt
/// and the data receipt will be bigger than max_receipt_size. The receipt should be rejected, but
/// currently isn't because of a bug (See https://github.com/near/nearcore/issues/12606)
/// Creates the following promise DAG:
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L216-266)
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

**File:** core/parameters/res/runtime_configs/69.yaml (L8-9)
```yaml
max_receipt_size: {old: 4_294_967_295, new: 4_194_304}
new_transactions_validation_state_size_soft_limit: {old: 4_294_967_295, new: 572_864}
```
