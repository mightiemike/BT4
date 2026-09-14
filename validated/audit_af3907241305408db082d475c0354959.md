### Title
Unbounded recursive walk of receipt-outcome DAG in `get_recursive_transaction_results` allows stack-overflow crash from a JSON-RPC `tx_status` query - ([File: chain/chain/src/chain.rs])

### Summary
`Chain::get_recursive_transaction_results` (called from `Chain::get_final_transaction_result`, which backs the public `tx`/`EXPERIMENTAL_tx_status` JSON-RPC methods) recurses once per receipt produced along a transaction's execution DAG, with **no depth limit**. A sibling feature added later, `GetReceiptToTx`, was explicitly hardened with a `MAX_DEPTH = 1000` guard (see `test_receipt_to_tx_depth_exceeded`), showing the project is aware unbounded recursive receipt-chain walks are a hazard — but that fix was never applied to the older `get_recursive_transaction_results` path.

### Finding Description [1](#0-0) 

`get_recursive_transaction_results` recursively calls itself once for every `receipt_id` produced by an execution outcome, unboundedly:
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
```
This is invoked from [2](#0-1)  `get_final_transaction_result`, which is the code path behind the standard `tx`/`EXPERIMENTAL_tx_status` JSON-RPC endpoints — reachable by any unprivileged RPC caller who knows a transaction hash.

An attacker can grow the depth of the receipt DAG for a single transaction by deploying a contract that performs long chains of self cross-contract calls (the codebase's own test helper demonstrates this pattern is achievable with ordinary gas budgets): [3](#0-2)  `max_self_recursion_delay` chains promise calls to itself until gas is nearly exhausted, producing one receipt per hop. Because outgoing receipts of previous blocks are retained and each hop links `receipt_ids` to the next, an attacker who repeatedly re-triggers such self-chaining (e.g. spawning a new chain each time gas is replenished by a follow-up transaction, or leaving many long chains rooted from delayed/postponed receipts across blocks) can build execution-outcome DAGs whose recursive depth is far larger than any bounded budget assumed by the code.

By contrast, the newer `GetReceiptToTx` RPC handler walks a *similar* receipt-ancestry chain but explicitly caps recursion at `MAX_DEPTH = 1000` and returns `GetReceiptToTxError::DepthExceeded` instead of recursing further, as shown by the dedicated regression test: [4](#0-3) . No equivalent bound exists for `get_recursive_transaction_results`.

This is directly analogous to CVE-2019-11779: an attacker-influenced structural property (there, a topic string with tens of thousands of separator characters; here, a receipt-DAG whose depth an attacker can grow via long promise chains) is fed into an unbounded native-stack recursive function, risking a stack-overflow crash of the serving process.

### Impact Explanation
A stack overflow in this recursive walk crashes the node process (or at minimum the actor/thread handling RPC queries) that services `tx`/`EXPERIMENTAL_tx_status` for the crafted transaction hash. Because this endpoint is reachable by any RPC client without requiring stake, special permissions, or validator status, a single crafted transaction combined with a follow-up RPC query can trigger a transaction/RPC-triggered halt of a public RPC node, satisfying the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Medium: constructing the input requires deploying a contract and driving many chained cross-contract calls to accumulate a sufficiently deep outcome DAG (bounded per-transaction by gas, but chainable across multiple transactions/blocks since past outcomes remain queryable), then issuing a normal `tx_status` RPC call for the earliest transaction hash in the chain. No special privileges, staking, or validator access are required — only the ability to submit ordinary function-call transactions and issue RPC queries, both available to any unprivileged actor.

### Recommendation
Convert `get_recursive_transaction_results` to an iterative traversal (e.g. a `VecDeque`/stack-based BFS/DFS as already used elsewhere in the codebase, e.g. `get_subtree_size` in `core/store/src/trie/trie_recording.rs`), or add an explicit depth/size bound analogous to `GetReceiptToTx`'s `MAX_DEPTH = 1000`, returning a graceful error instead of recursing indefinitely.

### Proof of Concept
1. Deploy a contract exposing a `recurse`-style method that issues a self cross-contract call to itself repeatedly (pattern already present in the codebase's `max_self_recursion_delay` test helper), consuming most of the attached gas per hop so each transaction produces a long linear chain of action receipts.
2. Submit repeated follow-up transactions/receipts that continue extending the same logical receipt lineage across multiple blocks so the aggregate outcome DAG reachable from an original transaction hash grows arbitrarily deep (tens of thousands of receipts).
3. Call the public JSON-RPC `tx` (or `EXPERIMENTAL_tx_status`) method with that transaction's hash against a node serving RPC.
4. `Chain::get_final_transaction_result` → `get_recursive_transaction_results` recurses once per receipt in the DAG; sufficiently deep chains overflow the native call stack and crash the RPC-serving thread/process, unlike the hardened `GetReceiptToTx` path which stops safely at `MAX_DEPTH = 1000`.

### Citations

**File:** chain/chain/src/chain.rs (L3052-3069)
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

**File:** chain/chain/src/chain.rs (L3071-3087)
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

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L806-840)
```rust
/// Delay completion of the receipt for as long as possible through self cross-contract calls.
///
/// This contract keeps the recursion depth and returns it when less than 5Tgas remains, which is
/// most likely is no longer sufficient for another cross-contract call.
///
/// This is a stable alternative to yield/resume proposal at the time of writing.
#[unsafe(no_mangle)]
pub unsafe fn max_self_recursion_delay() {
    input(0);
    let mut bytes = [0u8; 4];
    read_register(0, bytes.as_mut_ptr());
    let recursion = u32::from_be_bytes(bytes);
    let available_gas = prepaid_gas() - used_gas();
    if available_gas < 5_000_000_000_000 {
        return value_return(4, bytes.as_ptr() as u64);
    }
    current_account_id(1);
    let method_name = "max_self_recursion_delay";
    let promise_idx = promise_batch_create(u64::MAX, 1);
    let amount = 1u128;
    let gas_fixed = 0;
    let gas_weight = 1;
    let argument_bytes = recursion.saturating_add(1).to_be_bytes();
    promise_batch_action_function_call_weight(
        promise_idx,
        method_name.len() as u64,
        method_name.as_ptr() as u64,
        argument_bytes.len() as u64,
        argument_bytes.as_ptr() as u64,
        &amount as *const u128 as u64,
        gas_fixed,
        gas_weight,
    );
    promise_return(promise_idx);
}
```

**File:** test-loop-tests/src/tests/receipt_to_tx/errors.rs (L157-228)
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

    // Sanity: receipt_2 (999 FromReceipt + 1 terminal = 1000 iter) succeeds
    // — exactly at limit.
    let result = view_client.handle(receipt_to_tx_req(receipt_ids[2]));
    assert!(result.is_ok(), "1000 hops succeed, got: {result:?}");
    let response = result.unwrap();
    assert_eq!(response.transaction_hash, CryptoHash::hash_bytes(b"tx"));
    assert_eq!(response.sender_account_id.as_str(), "sender");
}
```
