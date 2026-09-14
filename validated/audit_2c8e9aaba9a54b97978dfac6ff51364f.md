Root cause confirmed: `runtime/runtime/src/lib.rs:1152-1170` appends `output_data_receivers` to an already-validated (against `max_receipt_size`) receipt *after* `validate_receipt(..., NewReceipt)` has run in `runtime/runtime/src/lib.rs:970` (called inside the action-execution loop at line 969-979, which happens **before** the output-data-receiver extension logic runs later in the same function). This is a documented, tracked bug (nearcore issue #12606) with an explicit workaround comment in `runtime/runtime/src/congestion_control.rs:413-427` (`try_forward`) and in `runtime/runtime/src/verifier.rs:733-739` (`ValidateReceiptMode::ExistingReceipt`).

### Title
Missing size re-validation after `output_data_receivers`/return-value are appended lets receipts exceed `max_receipt_size` - (File: `runtime/runtime/src/lib.rs`)

### Summary
`validate_receipt` enforces `max_receipt_size` on a newly-created receipt [1](#0-0) , and this check is invoked right after each action executes and *before* the receipt's `output_data_receivers` are populated [2](#0-1) . Later in the same function, if the action receipt has `output_data_receivers`, the runtime either extends an already-created new receipt's `output_data_receivers` in place (promise-return case) or appends `DataReceipt`s carrying the full returned value (value-return case) — none of this is re-checked against `max_receipt_size` [3](#0-2) . A contract can therefore submit a `FunctionCall` whose args/return value are sized to sit exactly at `max_receipt_size`, then have `output_data_receivers` or a large `value_return` appended afterward, producing a receipt whose serialized size exceeds the protocol's hard limit without triggering `ReceiptSizeExceeded`.

### Finding Description
This mirrors the ONFT `compose_msg` bug class: a message/payload composition path lacks a size check at the point where the final oversized artifact is actually assembled, even though an earlier size check exists on a component. In nearcore, `validate_receipt` is nearcore's analog of the ONFT `encode()` size guard, and it correctly checks the receipt as of `apply_action` completion [4](#0-3) . But the receipt is subsequently mutated — `output_data_receivers` are extended via `extend_from_slice` for `ReturnData::ReceiptIndex` results, or new `Data` receipts carrying the full return value are pushed — after that check has already passed [5](#0-4) . There is no subsequent re-validation of `max_receipt_size` on the mutated/new receipts before they are handed to `ReceiptSink`. The nearcore team is aware of this exact class of bug and has left a workaround comment referencing the tracking issue directly in the forwarding path: `try_forward` explicitly clamps any receipt above `max_receipt_size` down to `max_receipt_size` "to avoid receipts getting stuck," citing `https://github.com/near/nearcore/issues/12606` [6](#0-5) , and `validate_receipt`'s `ExistingReceipt` mode exists specifically "to handle them gracefully until the receipt size limit bug is fixed" [7](#0-6) .

### Impact Explanation
An oversized receipt that slips past `max_receipt_size` validation is a state-witness/protocol-limit violation: `max_receipt_size` exists specifically to bound `ChunkStateWitness` size so it stays under ~21MiB and so cross-shard receipt proofs stay within `outgoing_receipts_usual_size_limit`/`outgoing_receipts_big_size_limit` [8](#0-7) . Because the size clamp in `try_forward` only affects the *accounting* used for admission decisions and not the actual bytes forwarded, an oversized receipt can still be pushed into `outgoing_receipts` and, from there, into chunk state witnesses and cross-shard receipt proofs, growing the real witness size beyond the limits the protocol was designed to guarantee. This can degrade chunk distribution/validation reliability and, in the worst case, contribute to a chunk that a subset of validators cannot handle consistently, i.e. a state-transition/liveness risk directly reachable by an unprivileged contract deployer via ordinary `FunctionCall`/`promise_return`/`value_return` usage — no privileged access required.

### Likelihood Explanation
High from a reachability standpoint: any account can deploy a WASM contract and call methods using `promise_return` or `value_return` with return payloads sized just under `max_receipt_size`, exactly as nearcore's own regression tests do (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`) [9](#0-8) . These tests are explicitly written to demonstrate the receipt is *not* rejected ("The receipt should be rejected, but currently isn't because of a bug") [10](#0-9) .

### Recommendation
Re-run `validate_receipt(..., ValidateReceiptMode::NewReceipt)` (or an equivalent size check) on every receipt in `result.new_receipts` after `output_data_receivers`/data receipts are appended in `runtime/runtime/src/lib.rs`, immediately before receipts are handed off to `ReceiptSink`, so `ReceiptSizeExceeded` is raised deterministically rather than relying on the size-clamp workaround inside `try_forward`. Consider bounding `max_length_returned_data` combined with `output_data_receivers.len()` at the `value_return`/`promise_return` host-function call sites (`runtime/near-vm-runner/src/wasmtime_runner/logic.rs`) so the violation is caught synchronously during execution rather than discovered after the fact in the runtime.

### Proof of Concept
Use nearcore's own existing regression tests, which already reproduce this exact condition and assert the bug via `assert_oversized_receipt_occurred`:
- `test_max_receipt_size_promise_return` [11](#0-10)  — a contract builds a promise DAG `[C -then-> B]`; promise `C`'s receipt is sized to exactly `max_receipt_size`, then `output_data_receivers` is appended, pushing it over the limit.
- `test_max_receipt_size_value_return` [12](#0-11)  — `return_large_value` returns a value of size `max_receipt_size`, which is wrapped in a `DataReceipt` that itself exceeds `max_receipt_size`.
Both tests call `assert_oversized_receipt_occurred`, which scans chain state for an incoming/outgoing receipt whose borsh-serialized size exceeds `max_receipt_size` and succeeds only if one is found [13](#0-12) , confirming oversized receipts are accepted and propagated by the network today.

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

**File:** runtime/runtime/src/congestion_control.rs (L412-427)
```rust
    ) -> Result<ReceiptForwarding, RuntimeError> {
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

**File:** docs/misc/state_witness_size_limits.md (L16-38)
```markdown
* `max_receipt_size - 4 MiB`:
  * All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected.
  * Previously there was no limit on receipt size. Set to 4MiB, might be reduced to 1.5MiB in the future to match the transaction limit.
* `max_receipt_total_input_size - 4 MiB + 640 B`
  * Hard limit on the combined size of a receipt's resolved promise inputs (the `ReceivedData` referenced by its `input_data_ids`). Receipts which exceed it fail with `TotalPromiseInputSizeExceeded` without executing their actions.
  * These inputs are read before `per_receipt_storage_proof_size_limit` starts counting, so without this limit a single receipt could pull `max_number_input_data_dependencies * max_receipt_size` (128 * 4 MiB) into the witness.
  * The limit is `max_length_returned_data` (4 MiB) plus the worst-case per-input framing overhead (128 * 5 bytes), so 4 MiB of input data always fits no matter how it's split across data receipts.
* `combined_transactions_size_limit - 4 MiB`
  * Hard limit on total size of transactions from this and previous chunk. `ChunkStateWitness` contains transactions from two chunks, this limit applies to the sum of their sizes.
* `new_transactions_validation_state_size_soft_limit - 500 KiB`
  * Validating new transactions generates storage proof (recorded trie nodes), which has to be limited. Once transaction validation generates more storage proof than this limit, the chunk producer stops adding new transactions to the chunk.
* `per_receipt_storage_proof_size_limit - 4 MB`
  * Executing a receipt generates storage proof. A single receipt is allowed to generate at most 4MB of storage proof. This is a hard limit, receipts which generate more than that will fail.
* `main_storage_proof_size_soft_limit - 4 MB`
  * This is a limit on the total size of storage proof generated by receipts in one chunk. Once receipts generate more storage proof than this limit, the chunk producer stops processing receipts and moves the rest to the delayed queue.
  * It's a soft limit, which means that the total size of storage proof could reach 8 MB (3.99MB + one receipt which generates 4MB of storage proof)
  * Due to implementation details it's hard to find the exact amount of storage proof generated by a receipt, so an upper bound estimation is used instead. This upper bound assumes that every removal generates additional 2000 bytes of storage proof, so receipts which perform a lot of trie removals might be limited more than theoretically applicable.
* `outgoing_receipts_usual_size_limit - 100 KiB`
  * Limit on the size of outgoing receipts to another shard. Needed to keep the size of `source_receipt_proofs` small.
  * On most block heights a shard isn't allowed to send receipts larger than 100 KiB to another shard.
* `outgoing_receipts_big_size_limit - 4.5 MiB`
  * On every block height there's one special "allowed shard" which is allowed to send larger receipts, up to 4.5 MiB in total.
  * A receiving shard will receive receipts from `num_shards - 1` shards using the usual limit and one shard using the big limit.
```

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L124-267)
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
