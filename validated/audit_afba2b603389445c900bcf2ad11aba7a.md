### Title
Uncontrolled recursion in `get_recursive_transaction_results` allows a transaction-triggered stack overflow / crash of RPC nodes serving `tx`, `tx_status`, `EXPERIMENTAL_tx_status` - ([File: chain/chain/src/chain.rs])

### Summary
`chain/chain/src/chain.rs` implements the transaction/receipt result assembly used by the JSON-RPC `tx`, `tx_status`, and `EXPERIMENTAL_tx_status` methods with an unbounded, self-referential recursive function, `get_recursive_transaction_results`. It walks the DAG of `receipt_ids` produced by an execution outcome by calling itself once per child receipt id, with no depth limit and no iterative fallback [1](#0-0) . This is structurally the same bug class as CVE-2018-20994 (CWE-674, uncontrolled recursion): an attacker-controlled, self-referential/looping structure is walked with plain function recursion instead of an iterative or depth-bounded algorithm.

### Finding Description
`get_recursive_transaction_results` fetches the outcome for a receipt/transaction id, appends it to the `outcomes` vector, and then recurses once for every entry in `outcome.receipt_ids`, i.e. depth is driven directly by the length of the chain of `SuccessReceiptId` links a transaction produces [2](#0-1) . It is invoked from `get_final_transaction_result`, whose doc comment states it "Returns `FinalExecutionOutcomeView` for the given transaction. Waits for the end of the execution of all corresponding receipts", i.e. it is the code responsible for producing the transaction result returned to RPC callers [3](#0-2) . The comment "Fails if there are non executed receipts, and require_all_outcomes == true" indicates the same recursive routine (with `require_all_outcomes=false`) is also reused by the "partial"/in-progress result path used while polling `tx`/`tx_status`, so both the final and in-flight code paths for these RPC methods are affected.

Nowhere in this routine is there a depth counter, a visited-set, or an iterative worklist — unlike the sibling `ReceiptToTx` lookup path, which the codebase explicitly hardens against exactly this class of bug with an enforced `MAX_DEPTH = 1000` and a `DepthExceeded` error [4](#0-3) . This shows the project is aware of, and defends against, unbounded receipt-chain traversal in one place but not in `get_recursive_transaction_results`.

The depth an attacker can build is not limited to a single chunk's gas budget: each hop in a `SuccessReceiptId` chain corresponds to a separately-gassed receipt, potentially spread across many blocks/chunks over time (e.g., a contract that on every call creates exactly one follow-up receipt to itself, chained transaction after transaction). The existing test `slow_test_self_delay` demonstrates that even a single chunk's gas budget already allows chains on the order of ~56–221 receipts deep purely from self-recursive cross-contract calls [5](#0-4) ; nothing stops a caller from repeating this pattern across many chunks/blocks to build an arbitrarily deep chain over time, since the chain is persisted receipt-by-receipt in the execution-outcome column and only walked later, on demand, when someone queries `tx_status`.

### Impact Explanation
Any JSON-RPC caller who queries `tx`, `tx_status`, or `EXPERIMENTAL_tx_status` for a transaction whose receipt DAG contains a sufficiently long `SuccessReceiptId` chain will cause the serving RPC/view-client node to recurse to that depth. Because this is native Rust call-stack recursion (not heap-based), a sufficiently long chain will exhaust the thread stack and crash the process (or at minimum abort the actor/thread handling the RPC request), which is a transaction-triggered denial-of-service against nodes serving RPC queries — directly analogous to the stack-overflow-via-uncontrolled-recursion impact described in the trust-dns-proto advisory (CWE-674, `CVSS:3.0/.../A:H`).

### Likelihood Explanation
- Building the malicious receipt chain requires nothing beyond permissionless capabilities available to any account: repeatedly submitting self-recursive `FunctionCall`/promise transactions (already demonstrated feasible in-repo up to hundreds of hops within a single chunk) and repeating this pattern over enough blocks to accumulate a very long chain, well past the 1000-hop threshold the project itself treats as dangerous for the analogous `ReceiptToTx` structure.
- Triggering the crash then requires nothing more than an unauthenticated `tx_status`/`tx`/`EXPERIMENTAL_tx_status` JSON-RPC call, which is one of the most basic and widely used RPC methods, callable by anyone including the attacker.
- The bug is unconditionally reachable — there is no config flag or feature gate around `get_recursive_transaction_results`.

### Recommendation
Convert `get_recursive_transaction_results` to an iterative (worklist/stack-based) traversal, and additionally impose an explicit maximum depth/outcome count (mirroring the `MAX_DEPTH = 1000` pattern already used for `ReceiptToTx` lookups), returning a bounded error (e.g. `DepthExceeded`) instead of recursing without limit when a chain grows too long.

### Proof of Concept
1. Deploy a contract whose method, on invocation, issues exactly one outgoing `FunctionCall` promise back to itself (or to another controlled account), forming a linear `SuccessReceiptId` chain across successive receipts/blocks (as already exercised by the `max_self_recursion_delay` test method) [6](#0-5) .
2. Repeat the invocation across many chunks/blocks so that the resulting chain of chained receipts (linked purely via `SuccessReceiptId`) grows to several thousand entries, far exceeding depths the codebase elsewhere treats as unsafe (`MAX_DEPTH = 1000` for the analogous `ReceiptToTx` walk) [7](#0-6) .
3. Call the `tx`, `tx_status`, or `EXPERIMENTAL_tx_status` JSON-RPC endpoint for the originating transaction hash; this drives `get_final_transaction_result` → `get_recursive_transaction_results`, which recurses once per chained receipt with no depth guard [8](#0-7) , exhausting the serving thread's stack and crashing the RPC node.

### Citations

**File:** chain/chain/src/chain.rs (L3190-3225)
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

**File:** integration-tests/src/tests/runtime/test_evil_contracts.rs (L91-127)
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
```
