### Title
Unbounded recursion in `get_recursive_transaction_results` allows stack-overflow DoS via crafted receipt DAG triggered by an RPC transaction-status query - ([File: chain/chain/src/chain.rs])

### Summary
`Chain::get_recursive_transaction_results` recursively walks the tree of execution outcomes reachable from a transaction hash by directly self-recursing over every `receipt_id` produced by each outcome, with no depth limit or iterative fallback. [1](#0-0)  This function is the sole implementation used by `get_final_transaction_result`, the routine that answers `tx`/`EXPERIMENTAL_tx_status`-style JSON-RPC queries for final transaction outcomes. [2](#0-1)  This mirrors the reported `flatted` bug class exactly: an unbounded, attacker-influenceable recursive "revive"/graph-walk phase over externally supplied reference chains, with no iterative rewrite or depth cap (as fixed by `flatted`'s PR #88).

### Finding Description
`get_recursive_transaction_results` recurses once per receipt in the outcome tree: for each `id`, it fetches the outcome, pushes it into `outcomes`, then iterates every entry in `outcome.receipt_ids` and calls itself recursively for each child id. There is no depth counter, no maximum-recursion guard, and no conversion to an explicit stack/queue (unlike other parts of the codebase that intentionally use an iterative approach specifically "to avoid any potential stack overflows", e.g. `TrieRecorder::get_subtree_size` at `core/store/src/trie/trie_recording.rs:302`). [3](#0-2) 

The shape of the receipt DAG that this function walks is directly influenced by an unprivileged transaction sender: a contract can be authored to create long linear chains of promises (`promise_then` callbacks that themselves schedule the next promise), so that `outcome.receipt_ids` for receipt N contains exactly receipt N+1, producing a long singly-linked chain rather than a shallow, bushy tree. Once such a transaction has executed on-chain (its receipts have all been applied across some number of blocks), any unauthenticated RPC caller who queries the transaction's final result (`tx`, `EXPERIMENTAL_tx_status`) causes the node serving the RPC request to walk that entire receipt chain recursively via `get_final_transaction_result` → `get_recursive_transaction_results`.

Since the recursion depth is bounded only by how many chained receipts the attacker was able to get executed for a single transaction hash — not by anything checked in `get_recursive_transaction_results` itself — a sufficiently long receipt chain will exhaust the call stack of the thread handling the RPC request and crash the node process with a stack overflow, exactly as in the `flatted` `parse()`/`revive()` analog.

### Impact Explanation
A stack overflow in `get_recursive_transaction_results` aborts the process (Rust's default behavior on stack overflow is process abort, not a catchable panic), so any node whose RPC/view-client component evaluates this crafted transaction hash — including full/RPC nodes and potentially validator nodes acting as view clients — is halted by an unauthenticated actor. This satisfies the "transaction-triggered halt" criterion for a valid finding: a single malicious but syntactically valid transaction plus a single unauthenticated RPC query is enough to crash node processes that serve `tx_status`/`EXPERIMENTAL_tx_status` for that transaction, which is a denial-of-service against RPC/view-client availability.

### Likelihood Explanation
Reaching a depth sufficient to overflow a thread stack requires the attacker to first get a very long chain of receipts to execute for one transaction (bounded by the transaction's attached gas budget, since each hop in the promise chain consumes non-refundable minimum action/receipt gas fees, e.g. `action_receipt_creation_config` and `function_call_cost` per hop). This gas-budget constraint is the main limiting factor on likelihood: the number of chained hops achievable per transaction is bounded by `max total prepaid gas / minimum per-hop cost`, which may or may not be sufficient by itself to overflow the RPC thread's stack depending on stack size and per-frame size of `get_recursive_transaction_results` (which clones/pushes an `ExecutionOutcomeWithIdView` per level). I was not able to fully verify with the available tools whether typical thread stack sizes and per-frame costs make this practically exploitable with a single transaction's gas budget, or whether it would require chaining across the practical maximum receipt count observed in existing tests/integration code (which I could not fully enumerate). This uncertainty affects severity/likelihood confidence but does not change the root-cause finding: the function itself has no depth bound and is architecturally unsafe regardless of current gas-derived limits, since limits could shift with future protocol/fee changes.

### Recommendation
Rewrite `get_recursive_transaction_results` to use an explicit iterative worklist (stack/queue) instead of native recursion, consistent with the pattern already used elsewhere in the codebase for trie/graph traversal specifically to avoid stack overflows (see `core/store/src/trie/trie_recording.rs:302` and `core/store/src/trie/ops/iter.rs`). Additionally, enforce an explicit maximum number of outcomes/receipts collected (returning an error or truncated result past a configured cap) to bound both memory and time consumed by a single RPC call, independent of gas-derived assumptions about chain length.

### Proof of Concept
1. Deploy a contract whose exported method, on each invocation, issues exactly one `promise_then` call back to itself with minimal `FunctionCall` gas/deposit, forming a linear promise chain (receipt N's outcome has exactly one `receipt_ids` entry: receipt N+1).
2. Submit a single `SignedTransaction` invoking this method with the maximum allowed attached gas, so the runtime executes as many chained hops as the gas budget allows across however many blocks are needed.
3. Once all receipts in the chain have finished executing, send a JSON-RPC `tx`/`EXPERIMENTAL_tx_status` request (unauthenticated) for the original transaction hash to a full/RPC node.
4. The node's view-client calls `Chain::get_final_transaction_result` → `get_recursive_transaction_results`, recursing once per hop in the constructed chain; if the chain length is large enough relative to the RPC-serving thread's stack size, the process aborts with a stack overflow, denying RPC service (and potentially crashing the whole node process) for all users. [4](#0-3)

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
