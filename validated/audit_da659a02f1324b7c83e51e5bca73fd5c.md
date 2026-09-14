### Title
Receipts can exceed `max_receipt_size` after validation via `output_data_receivers`/value-return, breaking a documented hard limit relied on by congestion control and state-witness sizing - (File: `runtime/runtime/src/congestion_control.rs`)

### Summary
The CVE-2010-0734 analog here is a documented "maximum size" guarantee that downstream consumers rely on but which the producing component can silently violate. In nearcore, `max_receipt_size` is documented as a hard protocol limit ("All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected") [1](#0-0)  that other subsystems (bandwidth scheduling, congestion control, state-witness size budgeting) assume holds. However, the runtime has a known, currently-unfixed bug (tracked as near/nearcore#12606) where a receipt is validated against `max_receipt_size` before `output_data_receivers` is populated (promise-return case) or before a large returned value is wrapped into a `Data` receipt (value-return case), so the final serialized receipt can exceed the limit despite passing validation.

### Finding Description
`validate_receipt` in `runtime/runtime/src/verifier.rs` enforces `max_receipt_size` on newly created receipts, and `Runtime::apply_action`/`process_action_receipt` calls it in `runtime/runtime/src/lib.rs` on `new_result.new_receipts` right after action execution [2](#0-1) . But for promise-return chains and value-returns, the receipt/`output_data_receivers` fields are appended or the return value is wrapped into a `Data` receipt at a later point, after this check has already passed, so the resulting borsh-serialized receipt size is no longer re-validated. This is explicitly acknowledged as a bug in code comments and tests:

- `runtime/runtime/src/congestion_control.rs` `try_forward` contains a workaround: "There is a bug which allows to create receipts that are above the size limit... Let's pretend that all receipts are at most `max_receipt_size`" [3](#0-2) .
- The test suite directly reproduces this: `test_max_receipt_size_promise_return` builds a receipt at exactly `max_receipt_size`, then triggers `output_data_receivers` to be appended, pushing it over the limit without rejection [4](#0-3) , and `test_max_receipt_size_value_return` does the same for a large returned value wrapped as a `Data` receipt [5](#0-4) . Both tests explicitly assert that an oversized receipt does end up in the chain (`assert_oversized_receipt_occurred`) rather than being rejected [6](#0-5) .

The bug is analogous to CURL-CVE-2010-0734: a component (libcurl's write callback / nearcore's receipt pipeline) advertises and is documented to enforce a hard maximum size, but under specific conditions (post-processing after decompression / post-validation mutation of the receipt) the actual output silently exceeds that documented maximum, and every downstream consumer that assumes the bound (fixed buffers in the CVE; congestion/bandwidth accounting and state-witness size budgets here) can be affected.

### Impact Explanation
Downstream code paths assume `max_receipt_size` is a true hard cap:
- `ReceiptSinkV2::try_forward` clamps the size to `max_receipt_size` purely to avoid receipts getting permanently stuck in the outgoing buffer, but the clamp means congestion/bandwidth bookkeeping (`own_congestion_info`, per-link `OutgoingLimit`) is fed a smaller size than the receipt actually occupies on the wire and in the receiving shard's incoming-receipt storage [3](#0-2) .
- `outgoing_receipts_usual_size_limit` / `outgoing_receipts_big_size_limit` and the entire 21 MiB state-witness size budget documented in `docs/misc/state_witness_size_limits.md` are computed assuming `max_receipt_size` bounds every single receipt [7](#0-6) . An oversized receipt breaks that accounting, meaning the actual `ChunkStateWitness` (and the receipts embedded via `source_receipt_proofs`) can exceed the size that the bandwidth scheduler/congestion control believes is possible, undermining the invariant used to bound witness distribution cost and per-block resource usage.

This is a state-transition/robustness issue reachable by any unprivileged contract caller (no special privileges needed — any account deploying a contract that constructs a promise chain with `output_data_receivers` or a large returned value can trigger it), and it is already known/tracked by the nearcore team (issue #12606) rather than a novel discovery. The tests confirm the runtime does not crash on this and the observed effect is limited to receipts being propagated above the nominal size limit, not the more severe outcomes (unauthorized value movement, supply inflation, gas/fee bypass, or state-root divergence) that this scan is scoped to validate.

### Likelihood Explanation
High reachability (any transaction sender can trigger it via a normal cross-contract promise chain), but the currently known effect is bounded — the same-size clamp in `try_forward` and the demonstrated runtime resilience (`sum_4_tx` still succeeds after triggering the bug in `test_max_receipt_size`) show that this does not currently cause a crash, halt, or divergence between honest nodes in the traced code; it's already a documented workaround, not a novel exploitable path to unauthorized value movement or consensus divergence per the scan's acceptance criteria.

### Recommendation
Re-validate the final serialized receipt size against `max_receipt_size` at every point after mutation (specifically after `output_data_receivers` are populated in the promise-return path and after wrapping a returned value into a `Data` receipt), rather than only validating a receipt once immediately after `apply_action` and before any subsequent size-affecting mutation, closing the gap referenced by nearcore issue #12606.

### Proof of Concept
The existing repository tests already constitute a reproducible PoC:
- `test_max_receipt_size_promise_return` deploys a contract, constructs a promise DAG `[A -then-> B]` where `A` creates promise `C` and does a `promise_return`, sized so that `C`'s receipt is exactly `max_receipt_size` before `output_data_receivers` is added, then shows the size limit is violated [8](#0-7) .
- `test_max_receipt_size_value_return` triggers the same class of bug via a large returned value wrapped in a `Data` receipt [9](#0-8) .

Note: I was unable to fully trace the exact line in `runtime/runtime/src/lib.rs` where `output_data_receivers` is appended post-validation (only that 6 references exist in that file) due to index truncation; a full-repo Devin session would be needed to pin the exact statement performing the late mutation.

### Citations

**File:** docs/misc/state_witness_size_limits.md (L16-40)
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

In total that gives 4 MiB + 500 KiB + 8MB + 4 MiB + 5*100 KiB + 4.5 MiB ~= 21 MiB of maximum witness size. Possibly a little more on missing chunks.
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

**File:** test-loop-tests/src/tests/max_receipt_size.rs (L350-419)
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
```
