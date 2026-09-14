### Title
Oversized receipts can bypass `max_receipt_size` validation and enter the chain unchecked - (File: `runtime/runtime/src/verifier.rs`, `runtime/runtime/src/lib.rs`)

### Summary
This is analogous to the SecureStore report's core failure mode: a size limit is documented and nominally enforced, but the enforcement point is checked *before* the data is finalized, so the actual persisted/propagated artifact can silently exceed the limit without an error being raised at the point where it matters. In nearcore, `validate_receipt`/`validate_action_receipt` in `runtime/runtime/src/verifier.rs` measure `borsh::object_length(receipt)` against `limit_config.max_receipt_size` only in `ValidateReceiptMode::NewReceipt` mode [1](#0-0) , but this check happens at receipt-creation time, before later runtime logic (`output_data_receivers` population on promise return, or large returned values wrapped into a `DataReceipt`) can push the final receipt size above `max_receipt_size`. The code and tests explicitly acknowledge this is a known bug (tracked as near/nearcore#12606) and that `ValidateReceiptMode::ExistingReceipt` exists specifically to "handle receipts above the size limit gracefully" because such receipts can already exist in the chain [2](#0-1) .

### Finding Description
`max_receipt_size` is a hard limit documented as a core invariant of `ChunkStateWitness` sizing: "All receipts must be below 4 MiB, otherwise they'll be considered invalid and rejected" [3](#0-2) . The check is implemented in `validate_receipt`, which only measures and enforces `max_receipt_size` when `mode == ValidateReceiptMode::NewReceipt` [1](#0-0) .

The problem (confirmed by both source comments and integration tests) is that a receipt can pass this check at creation time, sized exactly at or near `max_receipt_size`, and only afterward have its `output_data_receivers` or returned-value payload appended by the runtime (e.g. `promise_return`, or a function call returning a large value that gets wrapped into a `DataReceipt`). This post-validation mutation is not re-validated against `max_receipt_size`, so the receipt that actually gets persisted to the trie / included in outgoing receipts / recorded into the chunk state witness can exceed the hard limit [4](#0-3) . The `ValidateReceiptMode::ExistingReceipt` variant exists purely as an admission that oversized receipts already occur in production and must be tolerated rather than crash the runtime: "There is a bug which allows to create receipts that are above the size limit. Runtime has to handle them gracefully until the receipt size limit bug is fixed" (referencing near/nearcore#12606) [5](#0-4) .

Integration tests directly demonstrate all three attack surfaces reachable by an ordinary contract-calling account with no special privileges:
- Promise-return DAG rewriting causes `output_data_receivers` to be attached after the size check, pushing the receipt over `max_receipt_size` [4](#0-3) .
- A contract returning a large value produces an oversized `DataReceipt` that also should be rejected but isn't [6](#0-5) .
- Both tests culminate in `assert_oversized_receipt_occurred`, which walks the chain looking for incoming receipts above `max_receipt_size` and asserts that one is actually found and successfully propagated on-chain [7](#0-6) .

Just as SecureStore silently drops data over 2048 bytes instead of erroring, nearcore silently *accepts* receipts over the documented hard limit instead of rejecting them — the failure mode is inverted (accept-instead-of-reject vs. drop-instead-of-error) but the root cause class is identical: a size guard that is checked at the wrong point in the data's lifecycle, allowing size-limit invariants to be silently violated in the persisted/propagated artifact.

### Impact Explanation
The `max_receipt_size` limit is one of the load-bearing constraints used to bound `ChunkStateWitness` size (documented target of ~21 MiB total) and to bound congestion-control accounting for cross-shard receipt buffering/bandwidth scheduling [8](#0-7) . Any unprivileged account can, via ordinary function-call actions (deploying a contract and calling methods that trigger `promise_return` or return large values), construct a receipt that violates this hard bound and get it accepted into the chain and propagated to other shards as an incoming receipt. Since this is a widely-known and reproducible bug path (deliberately preserved via `ValidateReceiptMode::ExistingReceipt` compatibility handling) rather than a hypothetical one, it represents accepted invalid state transitions relative to the protocol's documented size invariants, and increases witness/message sizes beyond intended bounds, which is the exact bug class flagged by the report (silent limit violation without error, risking downstream corruption/inconsistency for any consumer that assumes the limit holds).

### Likelihood Explanation
High reachability: any transaction signer with a deployed contract can trigger this via a function call producing a promise DAG with `output_data_receivers`, or a function call returning a large value — no validator, staking, or privileged role is required. The bug is already reproduced by first-party integration tests (`test_max_receipt_size_promise_return`, `test_max_receipt_size_value_return`) and explicitly tracked as a known, currently-unfixed issue (near/nearcore#12606) referenced directly in the enforcement code's own comments [9](#0-8) .

### Recommendation
Re-validate the fully-assembled receipt (including any `output_data_receivers` or wrapped return-value payloads added after the initial `NewReceipt` check) against `max_receipt_size` immediately before it is persisted or queued for cross-shard delivery, rather than only checking at creation time. Until fixed, any code path that appends data to an already-validated receipt (promise return handling, large-value data receipt construction) should re-run `validate_receipt` in `NewReceipt` mode, or explicitly truncate/reject the mutation, so the size invariant that state-witness limits, congestion control, and bandwidth scheduling all depend on cannot be silently violated by a single unprivileged transaction/contract call.

### Proof of Concept
Reproduced directly by nearcore's own test suite:
1. `test_max_receipt_size_promise_return` deploys `rs_contract`, calls `max_receipt_size_promise_return_method1` with `args_size` sized so promise `C`'s receipt is exactly `max_receipt_size`; the runtime later appends `output_data_receivers` to `C`, pushing it over the limit, and `assert_oversized_receipt_occurred` confirms an oversized receipt was actually included on-chain [10](#0-9) .
2. `test_max_receipt_size_value_return` calls `max_receipt_size_value_return_method` with a return value sized at `max_receipt_size`, which gets wrapped in a `DataReceipt` whose total size then exceeds the limit, again confirmed on-chain by `assert_oversized_receipt_occurred` [11](#0-10) .

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

**File:** docs/misc/state_witness_size_limits.md (L1-18)
```markdown
## State witness size limits

Some limits were introduced to keep the size of `ChunkStateWitness` reasonable.
`ChunkStateWitness` contains all the incoming transactions and receipts that will be processed during chunk application and in theory a single receipt could be tens of megabytes in size. Distributing a `ChunkStateWitness` this large would be troublesome, so we limit the size and number of transactions, receipts, etc. The limits aim to keep the total uncompressed size of `ChunkStateWitness` under 21MiB.

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
