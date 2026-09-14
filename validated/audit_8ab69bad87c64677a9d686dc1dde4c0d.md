### Title
Unbounded recursion in `get_recursive_transaction_results` allows RPC-triggered stack-overflow crash of an honest node - (File: `chain/chain/src/chain.rs`)

### Summary
`Chain::get_recursive_transaction_results` walks the outcome-DAG of a transaction by recursing once per `receipt_id` in every discovered execution outcome, with no depth limit. It is called by `get_final_transaction_result` and `get_partial_transaction_result_option`, both of which back the public `tx` / `EXPERIMENTAL_tx_status` JSON-RPC methods (via `ViewClientActor::get_tx_status` → `TxStatus` handler). An attacker who can get a sufficiently long chain of dependent receipts recorded on chain (via ordinary cross-contract calls, callbacks, or postponed/data receipts spread across many blocks) and then queries that transaction's status can drive this function into thousands of nested native stack frames, crashing the RPC/view-client thread of any honest node that answers the query.

### Finding Description
`get_recursive_transaction_results` is implemented as plain (non-tail) Rust recursion: [1](#0-0) 

Each recursive call pushes a new outcome onto `outcomes` and then recurses once per `receipt_ids[idx]` of that outcome, so the native call-stack depth equals the length of the longest receipt→receipt chain reachable from the queried transaction hash. There is no depth counter, no iteration limit, and no conversion to an explicit stack/queue (unlike other trie-walking code in this codebase, e.g. `TrieRecorder::get_subtree_size`, which explicitly documents converting to an iterative approach "to avoid any potential stack overflows" [2](#0-1) ).

This function is reachable from two public entry points:
- `get_final_transaction_result` — used when waiting for full completion of a transaction. [3](#0-2) 
- `get_partial_transaction_result_option` — used for partial/optimistic status, called directly from `ViewClientActor::get_tx_status`, the handler behind the `TxStatus` message and thus the `tx` JSON-RPC method available to any unprivileged RPC caller. [4](#0-3) [5](#0-4) 

The codebase already recognizes this exact bug class elsewhere: a sibling receipt-ancestry walk (`ReceiptToTx`) was hardened with an explicit `MAX_DEPTH=1000` and a `DepthExceeded` error, with a dedicated regression test confirming the fix: [6](#0-5) 

No equivalent bound exists for `get_recursive_transaction_results`, meaning the class of vulnerability that was patched in one code path remains present in another, older code path that also serves the most commonly used status/`tx` RPC method.

Receipt-outcome chains of this shape are attacker-producible without any privileged access: a sequence of cross-contract calls/callbacks (or a chain of postponed data receipts / promise-yield resumes across many blocks) each producing a receipt whose outcome's `receipt_ids` points to the next hop builds up a chain whose length is not bounded to a single transaction's gas budget — it accumulates over many transactions/blocks that all eventually get linked back to a single "root" transaction hash via `SuccessReceiptId`/receipt-outcome chaining, similar to the growth pattern the `ReceiptToTx` depth limit was introduced to stop.

### Impact Explanation
A sufficiently deep chain causes the OS thread evaluating `get_recursive_transaction_results` to overflow its stack, which in Rust aborts the process (there is no catchable panic for stack overflow). Since this runs on the `ViewClientActor` (a dedicated multithreaded actor answering RPC/view queries per `chain/jsonrpc/RPC_ARCHITECTURE.md`), any RPC/view node that answers a `tx` status query for a crafted transaction hash can be crashed, i.e., a transaction/RPC-triggered halt of an honest node — directly matching the "transaction-triggered halt" acceptance criterion. Because it is reachable purely from an unprivileged JSON-RPC caller invoking a widely used, standard RPC method (`tx`), this is a high-severity denial-of-service analog to CVE-2021-3382 (crash via crafted path/tree traversal), scoped strictly to the tolerated bug classes (no malicious peer/validator/network assumption required).

### Likelihood Explanation
Likelihood is Medium-High: no special privileges, staking, or validator status are required — only the ability to (a) get a deep receipt-outcome chain recorded (achievable through routine cross-contract-call patterns spread across blocks, similar to what the `ReceiptToTx` MAX_DEPTH fix was designed to guard against) and (b) issue a normal `tx`/`EXPERIMENTAL_tx_status` RPC query against a node that tracks the relevant shard. The main uncertainty (not fully verifiable from the available source) is the exact number of hops obtainable within realistic gas/time budgets before the native stack (default size, typically several MB per thread) is exhausted; frame size for this function is small, so a chain in the low thousands of hops is plausible, especially since the `ReceiptToTx` fix's own chosen threshold (1000) suggests the codebase's own estimate of what "attacker producible" chain depth looks like.

### Recommendation
Rewrite `get_recursive_transaction_results` to use an explicit heap-allocated worklist (stack/queue) instead of native recursion, mirroring the iterative pattern already used in `TrieRecorder::get_subtree_size` and the `FlattenNodesCrumb`/`CrumbStatus` iterative trie walks elsewhere in this codebase. Additionally, introduce an explicit maximum chain-depth/outcome-count bound (consistent with the `MAX_DEPTH=1000` already adopted for `GetReceiptToTx`) and return a typed error (e.g., `DepthExceeded`) rather than allowing unbounded traversal, for both `get_final_transaction_result` and `get_partial_transaction_result_option`.

### Proof of Concept
1. Deploy a contract that, on each call, schedules a cross-contract callback to itself (or uses `promise_yield`/data-receipt callbacks) so that each execution outcome's `receipt_ids` references exactly one further receipt.
2. Repeatedly invoke this pattern over many blocks so the resulting outcome DAG rooted at the original transaction hash forms a chain of several thousand hops (bounded only by patience/block production, not by a single transaction's gas limit).
3. Issue a standard `tx` (or `EXPERIMENTAL_tx_status`) JSON-RPC request for the original transaction hash against a node tracking the relevant shard.
4. `ViewClientActor::get_tx_status` → `Chain::get_partial_transaction_result_option`/`get_final_transaction_result` → `get_recursive_transaction_results` recurses once per hop; once chain depth exceeds the thread's stack capacity, the `ViewClientActor` thread overflows its stack and the node process aborts, denying service to all RPC clients of that node.

### Citations

**File:** chain/chain/src/chain.rs (L3188-3207)
```rust
    /// Collect all the execution outcomes existing at the current moment
    /// Fails if there are non executed receipts, and require_all_outcomes == true
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

**File:** chain/chain/src/chain.rs (L3254-3280)
```rust
    pub fn get_partial_transaction_result_option(
        &self,
        transaction_hash: &CryptoHash,
    ) -> Result<Option<FinalExecutionOutcomeView>, Error> {
        let transaction = self.chain_store.get_transaction(transaction_hash).ok_or_else(|| {
            Error::DBNotFoundErr(format!("Transaction {} is not found", transaction_hash))
        })?;
        let transaction = SignedTransactionView::from(Arc::unwrap_or_clone(transaction));

        let mut outcomes = Vec::new();
        self.get_recursive_transaction_results(&mut outcomes, transaction_hash, false)?;
        if outcomes.is_empty() {
            // The transaction is in the store (included in a chunk) but its execution outcome has
            // not been recorded yet, so there is no result to assemble.
            return Ok(None);
        }

        let status = self.get_execution_status(&outcomes, transaction_hash);
        let receipts_outcome = outcomes.split_off(1);
        let transaction_outcome = outcomes.pop().unwrap();
        Ok(Some(FinalExecutionOutcomeView {
            status,
            transaction,
            transaction_outcome,
            receipts_outcome,
        }))
    }
```

**File:** core/store/src/trie/trie_recording.rs (L302-304)
```rust
        // Non recursive approach to avoid any potential stack overflows.
        let mut queue: VecDeque<CryptoHash> = VecDeque::new();
        queue.push_back(*subtree_root);
```

**File:** chain/client/src/view_client_actor.rs (L700-716)
```rust
        // Check if we are tracking this shard.
        if self.shard_tracker.cares_about_shard(&head.prev_block_hash, target_shard_id) {
            match self.chain.get_partial_transaction_result_option(&tx_hash) {
                Ok(Some(tx_result)) => {
                    let status = self.get_tx_execution_status(&tx_result)?;
                    let res = if fetch_receipt {
                        let final_result =
                            self.chain.get_transaction_result_with_receipt(tx_result)?;
                        FinalExecutionOutcomeViewEnum::FinalExecutionOutcomeWithReceipt(
                            final_result,
                        )
                    } else {
                        FinalExecutionOutcomeViewEnum::FinalExecutionOutcome(tx_result)
                    };
                    let tx_status_view = TxStatusView { execution_outcome: Some(res), status };
                    Ok(TxStatusOutcome::Observed(Box::new(tx_status_view)))
                }
```

**File:** test-loop-tests/src/tests/receipt_to_tx/errors.rs (L157-176)
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
```
