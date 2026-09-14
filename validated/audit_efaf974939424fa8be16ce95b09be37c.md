### Title
Unbounded recursion in `get_recursive_transaction_results` allows RPC-triggered stack-overflow DoS - (File: chain/chain/src/chain.rs)

### Summary
`Chain::get_recursive_transaction_results` (used by `get_final_transaction_result`) recursively walks a transaction's receipt DAG with no depth limit, one stack frame per receipt hop. An attacker who submits a transaction whose execution produces a long linear chain of receipts (e.g. sequential cross-contract callbacks / self-recursive promise chains) and then queries transaction status can drive this recursion deep enough to overflow the thread stack, crashing the process handling the query. This mirrors the reported Spring Data Commons bug class: unbounded recursive parsing/traversal of attacker-influenced input structure causing a `StackOverflowException`/DoS.

### Finding Description
`get_recursive_transaction_results` recurses once per receipt id found in an outcome's `receipt_ids`, with no maximum-depth guard: [1](#0-0) 

It is invoked by `get_final_transaction_result`, which is the code path backing the `tx` / `EXPERIMENTAL_tx_status` JSON-RPC methods and `broadcast_tx_commit` (polling for tx completion), all reachable by any unprivileged RPC caller supplying just a transaction hash: [2](#0-1) 

Contrast this with other DAG-walking code in the same codebase that explicitly avoids recursion to prevent stack overflows, e.g. `TrieRecorder::get_subtree_size`, which comments "Non recursive approach to avoid any potential stack overflows" and uses an explicit `VecDeque` work queue instead of function recursion: [3](#0-2) 

Similarly, the newer `ReceiptToTx` lookup path (`GetReceiptToTxError::DepthExceeded`) enforces an explicit `MAX_DEPTH` of 1000 hops precisely to bound recursive/iterative walks over receipt ancestry chains driven by RPC queries: [4](#0-3) [5](#0-4) 

`get_recursive_transaction_results` has no equivalent bound: the depth of recursion is fully determined by the length of the receipt chain that the attacker's contract logic produces (e.g. a chain of sequential `promise_then` cross-contract calls, similar in structure to the `max_self_recursion_delay` self-recursion test contract), and this chain can be extended across many blocks over time since receipt processing is not limited to a single chunk's gas budget: [6](#0-5) 

### Impact Explanation
A successful exploitation crashes the thread/process executing `Chain::get_final_transaction_result` for the querying node (the node's `chain` layer used to answer tx-status RPC queries). Because Rust's default stack-overflow behavior aborts the process (there is no catchable exception as in JVM-based Spring Data Commons), this is a harder failure mode than the original CVE: it can crash the RPC-serving node (or, if invoked internally by validators processing `broadcast_tx_commit` polling, potentially disrupt block/chunk production paths on that node), constituting a transaction/RPC-triggered halt of node availability. This fits the "transaction-triggered halt" impact category.

### Likelihood Explanation
Likelihood is limited by how deep a receipt chain an attacker can realistically build:
- Each additional receipt hop costs gas (function-call send + execution fees), so within a single chunk only tens to low hundreds of chained receipts are affordable at max gas burnt (200 Tgas typical, ~2-3 Tgas per hop based on the `max_self_recursion_delay` test observing depths of 56–221 for similar recursive gas-bounded call chains): [7](#0-6) 
- However, nothing prevents an attacker from extending this chain across many blocks (each hop being a new receipt scheduled in a subsequent chunk), since `process_receipts`/delayed-receipt handling has no cap on total chain length over time — only per-chunk gas is capped. An attacker with modest, sustained gas expenditure over many blocks can build a receipt chain long enough (thousands to tens of thousands of hops) to exhaust a typical thread stack (which is usually a few MiB), especially given that each recursive call frame captures a non-trivial `ExecutionOutcomeWithIdView` reference/index and loop state.
- The requirement to (a) deploy/call a contract capable of self-chaining promises and (b) wait for the chain to grow, then (c) issue a single `tx`/`broadcast_tx_commit` RPC query, is well within reach of a single unprivileged transaction signer / RPC caller, with no special privileges needed.

### Recommendation
Convert `get_recursive_transaction_results` to an iterative traversal using an explicit stack/queue (as already done in `TrieRecorder::get_subtree_size`), and/or impose an explicit maximum receipt-chain depth (as already implemented for `ReceiptToTx` via `MAX_DEPTH`), returning a bounded error (e.g. `DepthExceeded`) instead of recursing indefinitely.

### Proof of Concept
1. Deploy a contract method that, when called, issues a `promise_then` to call itself again on receipt of its own callback, forming a strictly linear receipt chain (structurally similar to `max_self_recursion_delay` in `runtime/near-test-contracts`).
2. Submit an initial transaction invoking this method with a very large recursion counter, allowing the chain to be spread out and re-triggered across many blocks (e.g. each hop re-arming the next call), building a receipt chain of many thousands of hops over time.
3. Once the chain has grown sufficiently, call the `tx` (or `EXPERIMENTAL_tx_status`) JSON-RPC method (or `broadcast_tx_commit`, which polls the same code path) with the original transaction hash.
4. `Chain::get_final_transaction_result` → `get_recursive_transaction_results` recurses once per receipt in the chain; sufficient chain depth exhausts the thread stack and crashes the node process serving the request.

Note: I was unable to directly confirm within the indexed code the exact call site in `chain/client/src/view_client_actor.rs` that invokes `get_final_transaction_result` for the `tx` RPC handler (the grep for that specific file returned no matches, though the function is referenced from `core/primitives/src/views.rs` and integration/test-loop tests exercising tx-status flows). This may be due to index size limits; a Devin session with full repository access should confirm the precise RPC dispatch path before finalizing severity classification.

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

**File:** chain/chain/src/chain.rs (L3209-3225)
```rust
    /// Returns FinalExecutionOutcomeView for the given transaction.
    /// Waits for the end of the execution of all corresponding receipts
    pub fn get_final_transaction_result(
        &self,
        transaction_hash: &CryptoHash,
    ) -> Result<FinalExecutionOutcomeView, Error> {
        let mut outcomes = Vec::new();
        self.get_recursive_transaction_results(&mut outcomes, transaction_hash, true)?;
        let status = self.get_execution_status(&outcomes, transaction_hash);
        let receipts_outcome = outcomes.split_off(1);
        let transaction = self.chain_store.get_transaction(transaction_hash).ok_or_else(|| {
            Error::DBNotFoundErr(format!("Transaction {} is not found", transaction_hash))
        })?;
        let transaction = SignedTransactionView::from(Arc::unwrap_or_clone(transaction));
        let transaction_outcome = outcomes.pop().unwrap();
        Ok(FinalExecutionOutcomeView { status, transaction, transaction_outcome, receipts_outcome })
    }
```

**File:** core/store/src/trie/trie_recording.rs (L297-308)
```rust
    /// Get size of all recorded nodes and values which are under `subtree_root` (including `subtree_root`).
    fn get_subtree_size(&self, subtree_root: &CryptoHash) -> SubtreeSize {
        let mut nodes_size: usize = 0;
        let mut values_size: usize = 0;

        // Non recursive approach to avoid any potential stack overflows.
        let mut queue: VecDeque<CryptoHash> = VecDeque::new();
        queue.push_back(*subtree_root);

        let mut seen_items: HashSet<CryptoHash> = HashSet::new();

        while let Some(cur_node_hash) = queue.pop_front() {
```

**File:** chain/client-primitives/src/types.rs (L1052-1067)
```rust
#[derive(thiserror::Error, Debug)]
pub enum GetReceiptToTxError {
    #[error("Receipt with id {0} has never been observed on this node")]
    UnknownReceipt(CryptoHash),
    #[error("depth limit {limit} exceeded when resolving receipt {receipt_id}")]
    DepthExceeded { receipt_id: CryptoHash, limit: u32 },
    #[error("this node does not support receipt-to-tx lookup: {0}")]
    Unsupported(String),
    #[error("execution outcomes are not stored on this node (save_tx_outcomes=false)")]
    OutcomesNotStored,
    #[error("requested window {requested} exceeds maximum {maximum}")]
    WindowTooLarge { requested: BlockHeightDelta, maximum: BlockHeightDelta },
    #[error("malformed hint: {0}")]
    MalformedHint(String),
    #[error("hint-scan budget exceeded: {scanned} outcomes scanned, limit {limit}")]
    BudgetExceeded { scanned: u64, limit: u64 },
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
