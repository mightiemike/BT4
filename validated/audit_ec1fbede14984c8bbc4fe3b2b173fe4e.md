### Title
Unprivileged `FunctionCall` transactions can create receipts that exceed `max_receipt_size`, bypassing receipt-size validation - (File: `runtime/runtime/src/congestion_control.rs`, `test-loop-tests/src/tests/max_receipt_size.rs`)

### Summary
Any account can submit an ordinary, unprivileged `FunctionCall` transaction whose resulting action receipt ends up larger than the protocol's `max_receipt_size` limit, because the size check is only enforced at receipt-creation time and can be bypassed by post-creation mutations (appending `output_data_receivers`) or by large returned values wrapped into follow-up receipts. This is the same bug class as the external report: an unprivileged actor can insert a malformed/oversized "entry" (here, a receipt) into a shared queue structure that other logic (congestion control, bandwidth scheduler, outgoing-receipt forwarding) assumes is always bounded, and the codebase's own fix is an admitted "workaround" rather than a rejection of the bad input, tracked upstream as issue #12606.

### Finding Description
Receipts are validated for size when they are newly created during action execution: `apply_action_receipt` calls `validate_receipt(..., ValidateReceiptMode::NewReceipt)` right after each action runs [1](#0-0) . However, this check happens before later mutations to the receipt are applied. The in-repo regression test documents the exact bypass: a receipt is built at exactly `max_receipt_size`, passes validation, and only afterwards has `output_data_receivers` appended to it, pushing it above the limit — the comment explicitly states "The receipt should be rejected, but currently isn't because of a bug" [2](#0-1) . A second variant achieves the same effect purely through a large returned value that gets wrapped into a data receipt bigger than `max_receipt_size` [3](#0-2) .

Downstream, `ReceiptSinkV2::try_forward` explicitly documents that this bug exists and "patches" it only for the purpose of avoiding the receipt getting permanently stuck in the outgoing buffer, by pretending the receipt's size is `max_receipt_size` for admission/limit bookkeeping — the actual on-the-wire/on-trie receipt remains oversized: [4](#0-3) 

The same clamp-to-`max_receipt_size` workaround is repeated when generating bandwidth requests from buffered receipt groups [5](#0-4) , and is explicitly called out as an "oversized-receipt workaround" invariant note in the protocol spec, directly referencing issue #12606 [6](#0-5) .

This mirrors the Teller `commitCollateral()` bug class precisely: an unprivileged, permissionless call (`FunctionCall` action from any signer) inserts an element (an oversized receipt) into a shared, size-bounded data structure (the outgoing receipt buffer / delayed receipt queue) that other privileged/critical logic (congestion accounting, bandwidth scheduler size grants, forwarding admission) assumes is always within bounds. Rather than rejecting the malformed entry at the point of insertion, the system only patches the *symptom* (buffer admission) while the oversized receipt itself persists in state and is transmitted as-is.

### Impact Explanation
Because the actual persisted/transmitted receipt remains larger than `max_receipt_size` even though all size-based congestion/bandwidth accounting treats it as exactly `max_receipt_size`, this creates a systemic mismatch between the real byte cost of state (receipt_bytes, congestion memory dimension, bandwidth grants) and what the protocol's committed `CongestionInfo`/bandwidth bookkeeping records. The protocol's own invariants document assumes "all receipts are smaller than `max_receipt_size`" as a liveness guarantee [7](#0-6) ; an unprivileged transaction can violate that guarantee outright. This undermines chunk/state-witness size assumptions and the accuracy of consensus-committed congestion accounting, and is precisely the receipt-class DoS (oversized/malformed entries silently entering a shared queue relied upon by unrelated future operations) the external report flags as High severity.

### Likelihood Explanation
The trigger requires no special privilege — any account can deploy a contract (or use the existing test contract) and submit a single `FunctionCall` transaction that returns a large value or drives a specific promise-combinator pattern that appends `output_data_receivers` after size validation. This is fully reachable from a standard RPC-submitted transaction with a signed, valid access key; no validator, relayer, or network-level position is needed.

### Recommendation
- Re-validate receipt size (`ReceiptSizeExceeded`) *after* all mutations (including `output_data_receivers` attachment and return-value wrapping) are applied, not only immediately after action execution, so oversized receipts are rejected at creation instead of being smuggled into the delayed/buffered receipt queues.
- Remove reliance on the `try_forward`/`generate_bandwidth_requests` clamp-to-`max_receipt_size` workaround as the sole mitigation, since it hides rather than fixes the invariant violation; treat any receipt exceeding `max_receipt_size` found in these paths as `StorageInconsistentState`/fatal rather than silently clamping.

### Proof of Concept
Deploy the standard test contract and call `max_receipt_size_promise_return_method1` with `args_size` chosen so promise `C`'s receipt is exactly `max_receipt_size` bytes before validation; the runtime validates and accepts it, then appends `output_data_receivers`, pushing the persisted receipt above `max_receipt_size` — reproduced by the existing repo test `test_max_receipt_size_promise_return` [8](#0-7) . Alternatively, call `max_receipt_size_value_return_method` to return a value of size `max_receipt_size`, which becomes a Data receipt larger than the limit — reproduced by `test_max_receipt_size_value_return` [9](#0-8) . Both tests currently assert only that "the runtime shouldn't die" (`assert_oversized_receipt_occurred`), confirming the oversized receipt is accepted into the system rather than rejected.

### Citations

**File:** protocol-model/spec/runtime-execution.md (L67-67)
```markdown
4. **Execute actions in order** (`runtime/runtime/src/lib.rs:848`): for each action compute an `action_hash`, call `apply_action`, and on success validate every newly created receipt with `validate_receipt(..., NewReceipt)` (`:871`). `merge` folds the result; on the first `Err` the loop records the action index and breaks (`runtime/runtime/src/lib.rs:884`).
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

**File:** runtime/runtime/src/congestion_control.rs (L571-606)
```rust
    fn get_receipt_group_sizes_for_buffer_to_shard<'a>(
        &'a self,
        to_shard: ShardId,
        trie: &'a dyn TrieAccess,
        side_effects: bool,
        params: &BandwidthSchedulerParams,
    ) -> Box<dyn Iterator<Item = Result<u64, StorageError>> + 'a> {
        let outgoing_receipts_buffer_len = self.outgoing_buffers.buffer_len(to_shard).unwrap_or(0);

        if outgoing_receipts_buffer_len == 0 {
            // No receipts in the outgoing buffer, return an empty iterator.
            return Box::new(std::iter::empty());
        }

        // To make a proper bandwidth request we need the metadata for the outgoing buffer to be fully initialized
        // (i.e. contain data about all of the receipts in the outgoing buffer). There is a moment right after the
        // protocol upgrade where the outgoing buffer contains receipts which were buffered in the previous protocol
        // version where metadata was not enabled. Metadata doesn't contain information about them.
        // We can't make a proper request in this case, so we make a basic request while we wait for
        // metadata to become fully initialized. The basic request requests just `max_receipt_size`. This is enough to
        // ensure liveness, as all receipts are smaller than `max_receipt_size`. The resulting behavior is similar
        // to the previous approach where the `allowed_shard` was assigned most of the bandwidth.
        // Over time these old receipts will be removed from the outgoing buffer and eventually metadata will contain
        // information about every receipt in the buffer. From that point on we will be able to make
        // proper bandwidth requests.

        match self.outgoing_metadatas.get_metadata_for_shard(&to_shard) {
            Some(metadata) if metadata.total_receipts_num() == outgoing_receipts_buffer_len => {
                // Metadata fully initialized, use it to read receipt group sizes.
                Box::new(metadata.iter_receipt_group_sizes(trie, side_effects))
            }
            _ => {
                // Metadata not initialized. Make a basic request which requests only `max_receipt_size`.
                Box::new([Ok(params.max_receipt_size)].into_iter())
            }
        }
```

**File:** protocol-model/spec/cross-shard-congestion.md (L372-374)
```markdown
- **Oversized-receipt workaround**: receipts above `max_receipt_size` are treated as
  exactly `max_receipt_size` for both forwarding limits (`congestion_control.rs:417`)
  and bandwidth requests (`:561`) so they cannot get permanently stuck (issue #12606).
```
