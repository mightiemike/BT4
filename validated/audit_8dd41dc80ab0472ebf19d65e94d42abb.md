### Title
Unbounded native-stack recursion when resolving transaction execution outcomes - ([File: chain/chain/src/chain.rs])

### Summary
`Chain::get_recursive_transaction_results` (`chain/chain/src/chain.rs:3190-3207`) walks the tree of receipt outcomes produced by a transaction using genuine Rust function recursion (one native stack frame per receipt hop), with no depth counter and no conversion to an iterative/BFS traversal. This function backs `get_final_transaction_result` (`chain/chain/src/chain.rs:3211-3225`), which is reachable from the public JSON-RPC transaction-status API (`RpcTransactionStatusRequest` / `tx` / `EXPERIMENTAL_tx_status`, parsed in `chain/jsonrpc/src/api/transactions.rs:34-52`) using nothing more than a transaction hash supplied by any RPC caller.

### Finding Description
```rust
fn get_recursive_transaction_results(
    &self,
    outcomes: &mut Vec<ExecutionOutcomeWithIdView>,
    id: &CryptoHash,
    require_all_outcomes: bool,
) -> Result<(), Error> {
    let outcome = match self.get_execution_outcome(id) { ... };
    outcomes.push(ExecutionOutcomeWithIdView::from(outcome));
    let outcome_idx = outcomes.len() - 1;
    for idx in 0..outcomes[outcome_idx].outcome.receipt_ids.len() {
        let id = outcomes[outcome_idx].outcome.receipt_ids[idx];
        self.get_recursive_transaction_results(outcomes, &id, require_all_outcomes)?;
    }
    Ok(())
}
``` [1](#0-0) 

This is the same bug class as CVE-2019-6293 (`mark_beginning_as_normal` in flex): a function that recurses on itself once per unit of attacker-influenced structure, with the recursion depth controlled by data the attacker submits, and no explicit depth limit to bound native stack growth. Here the "structure" is the DFS-chain of receipt IDs produced by cross-contract/self promise calls originating from a single transaction. An account can submit one `FunctionCall` transaction whose contract logic issues a linear chain of self-recursive promise calls (`promise_then`/`promise_batch_create`), each producing exactly one child receipt id in its `ExecutionOutcome`. When any RPC caller subsequently queries the transaction status for that hash, `get_final_transaction_result` -> `get_recursive_transaction_results` recurses once per hop in that chain, consuming native stack on the RPC/view-client thread.

Notably, the developers were clearly aware of this exact class of bug elsewhere in the codebase: `test-loop-tests/src/tests/receipt_to_tx/errors.rs` documents a nearly identical `ReceiptToTx` chain-walk that was deliberately converted to an **iterative** traversal with an explicit `MAX_DEPTH = 1000` and a `DepthExceeded` error [2](#0-1) , and `core/store/src/trie/trie_recording.rs::get_subtree_size` was likewise rewritten as an explicit iterative BFS "to avoid any potential stack overflows" [3](#0-2) . `get_recursive_transaction_results` was not given the same treatment and remains plain recursion with no depth cap.

### Impact Explanation
A stack overflow in the process handling `get_final_transaction_result` would abort/crash the affected node thread (in Rust, stack overflow triggers process abort, not a catchable panic), producing a transaction-triggered halt of the node servicing that RPC/view-client request. Because this code path is part of the `Chain`/view-client logic shared by validating and RPC nodes, an attacker who can get any node to evaluate `tx`/`EXPERIMENTAL_tx_status` for a transaction hash that produced a sufficiently deep receipt chain can crash that node's process — a concrete transaction-triggered denial of service, matching the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Reachability requires: (1) an unprivileged account submitting a single `FunctionCall` transaction whose contract logic self-recurses via promises to build a deep, linear receipt chain, and (2) any caller (including automatic internal polling for transaction finality, or a third-party RPC query) invoking transaction-status resolution on that hash. Per-transaction attached gas bounds how many chained receipts a single transaction can generate (empirically on the order of tens to a few hundred hops based on `integration-tests/src/tests/runtime/test_evil_contracts.rs::slow_test_self_delay`, which observed depths of 56–221 for a maximum-gas self-recursive call) [4](#0-3) . This bounds the achievable native recursion depth to roughly a few hundred stack frames, which is unlikely by itself to overflow a typical multi-megabyte thread stack under normal per-frame sizes. The exact per-frame stack cost of `get_recursive_transaction_results` (which allocates and indexes into a growing `Vec<ExecutionOutcomeWithIdView>` per frame) and the actual stack size configured for the RPC/view-client thread were not verifiable from the indexed code, so I cannot conclusively confirm that the maximum achievable chain length actually overflows the stack in production configurations — this is the main source of uncertainty in this finding.

### Recommendation
Convert `get_recursive_transaction_results` to an iterative traversal (explicit work-queue/stack, as already done for `ReceiptToTx` resolution and trie subtree-size computation), and add an explicit maximum-depth/maximum-outcome-count guard that returns a well-defined error (mirroring the `DepthExceeded` pattern) instead of relying on native call-stack limits.

### Proof of Concept
1. Deploy a contract exposing a method that issues a self promise-chain, e.g. similar to the `max_self_recursion_delay`/`recurse` patterns used in `runtime/near-test-contracts/test-contract-rs/src/lib.rs:839-864` and `integration-tests/src/tests/runtime/test_evil_contracts.rs:91-131`, but using cross-contract promise calls (not just WASM call-stack recursion) so that each hop produces a new receipt id in the outcome tree.
2. Submit a `FunctionCall` transaction with maximum prepaid gas invoking that method, producing the deepest possible linear chain of receipt outcomes.
3. Call the JSON-RPC `tx`/`EXPERIMENTAL_tx_status` method (or trigger internal finality polling) with the transaction hash, causing the node to execute `Chain::get_final_transaction_result` → `get_recursive_transaction_results` and recurse once per receipt hop.
4. Observe stack growth proportional to chain depth; repeat/scale the experiment (e.g., across multiple transactions or larger gas limits/future protocol gas increases) to determine whether depth can be pushed far enough to overflow the RPC/view-client thread's stack and abort the node process.

### Citations

**File:** chain/chain/src/chain.rs (L3190-3207)
```rust
    fn get_recursive_transaction_results(
        &self,
        outcomes: &mut Vec<ExecutionOutcomeWithIdView>,
        id: &CryptoHash,
        require_all_outcomes: bool,
    ) -> Result<(), Error> {
        let outcome = match self.get_execution_outcome(id) {
            Ok(outcome) => outcome,
            Err(err) => return if require_all_outcomes { Err(err) } else { Ok(()) },
        };
        outcomes.push(ExecutionOutcomeWithIdView::from(outcome));
        let outcome_idx = outcomes.len() - 1;
        for idx in 0..outcomes[outcome_idx].outcome.receipt_ids.len() {
            let id = outcomes[outcome_idx].outcome.receipt_ids[idx];
            self.get_recursive_transaction_results(outcomes, &id, require_all_outcomes)?;
        }
        Ok(())
    }
```

**File:** test-loop-tests/src/tests/receipt_to_tx/errors.rs (L157-219)
```rust
/// Handler-level: write synthetic ReceiptToTx rows forming chain of 1001
/// FromReceipt entries (exceeds MAX_DEPTH=1000). Verify DepthExceeded
/// returned with originally queried receipt_id.
#[test]
fn test_receipt_to_tx_depth_exceeded() {
    init_test_logger();

    let mut env = TestLoopBuilder::new().epoch_length(EPOCH_LENGTH).track_all_shards().build();

    let store = env.validator().store();
    let mut store_update = store.store_update();

    // Chain of 1002 receipt IDs: receipt_0 → receipt_1 → ... → receipt_1001.
    // receipt_0..receipt_1000 are FromReceipt → next. receipt_1001 is
    // FromTransaction (terminal — never reached).
    let chain_len = 1002usize;
    let receipt_ids: Vec<CryptoHash> =
        (0..chain_len).map(|i| CryptoHash::hash_bytes(&(i as u32).to_le_bytes())).collect();

    // Terminal node: receipt_1001 → tx.
    store_update.insert_ser(
        DBCol::ReceiptToTx,
        receipt_ids[chain_len - 1].as_ref(),
        &ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
            origin: ReceiptOrigin::FromTransaction(ReceiptOriginTransaction {
                tx_hash: CryptoHash::hash_bytes(b"tx"),
                sender_account_id: "sender".parse().unwrap(),
            }),
            receiver_account_id: "receiver".parse().unwrap(),
            shard_id: ShardId::new(0),
        }),
    );

    // Intermediates: receipt_i → receipt_{i+1}.
    for i in 0..chain_len - 1 {
        store_update.insert_ser(
            DBCol::ReceiptToTx,
            receipt_ids[i].as_ref(),
            &ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
                origin: ReceiptOrigin::FromReceipt(ReceiptOriginReceipt {
                    parent_receipt_id: receipt_ids[i + 1],
                    parent_predecessor_id: "system".parse().unwrap(),
                }),
                receiver_account_id: "receiver".parse().unwrap(),
                shard_id: ShardId::new(0),
            }),
        );
    }

    store_update.commit();

    // Query receipt_0 — needs 1001 hops, exceeds MAX_DEPTH=1000.
    let handle = env.node_datas[0].view_client_sender.actor_handle();
    let view_client: &mut near_client::ViewClientActor = env.test_loop.data.get_mut(&handle);
    let result = view_client.handle(receipt_to_tx_req(receipt_ids[0]));

    match result {
        Err(GetReceiptToTxError::DepthExceeded { receipt_id, limit }) => {
            assert_eq!(receipt_id, receipt_ids[0], "error reports originally queried receipt");
            assert_eq!(limit, 1000, "limit == MAX_DEPTH=1000");
        }
        other => panic!("expected DepthExceeded error, got: {other:?}"),
    }
```

**File:** core/store/src/trie/trie_recording.rs (L297-304)
```rust
    /// Get size of all recorded nodes and values which are under `subtree_root` (including `subtree_root`).
    fn get_subtree_size(&self, subtree_root: &CryptoHash) -> SubtreeSize {
        let mut nodes_size: usize = 0;
        let mut values_size: usize = 0;

        // Non recursive approach to avoid any potential stack overflows.
        let mut queue: VecDeque<CryptoHash> = VecDeque::new();
        queue.push_back(*subtree_root);
```

**File:** integration-tests/src/tests/runtime/test_evil_contracts.rs (L91-131)
```rust
/// Test delaying the conclusion of a receipt for as long as possible through the use of self
/// cross-contract calls.
#[test]
fn slow_test_self_delay() {
    let node = setup_test_contract(near_test_contracts::rs_contract());
    let res = node
        .user()
        .function_call(
            "alice.near".parse().unwrap(),
            "test_contract.alice.near".parse().unwrap(),
            "max_self_recursion_delay",
            vec![0; 4],
            MAX_GAS,
            Balance::ZERO,
        )
        .unwrap();

    // The exact expected depth varies depending on the set of enabled features.
    // When test_features are enabled, the test contract becomes larger and the calls to it are more expensive.
    // When nightly is enabled, the gas costs change a bit.
    // The test makes sure that the depth is within the expected range, but it doesn't check an exact value
    // to avoid having separate cases for every possible combination of features.
    let min_expected_depth = 56;
    // The upper limit has been recently bumped to 221 from the previous value of 62 after the
    // adjustment of a function call gas costs.
    let max_expected_depth = 221;
    match res.status {
        FinalExecutionStatus::SuccessValue(depth_bytes) => {
            let depth = u32::from_be_bytes(depth_bytes.try_into().unwrap());
            assert!(
                depth >= min_expected_depth,
                "The function has recursed fewer times than expected: {depth} < {min_expected_depth}",
            );
            assert!(
                depth <= max_expected_depth,
                "The function has recursed more times than expected: {depth} > {max_expected_depth}",
            );
        }
        _ => panic!("Expected success, got: {:?}", res),
    }
}
```
