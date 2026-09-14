### Title
Unbounded recursion in `get_final_transaction_result` allows an attacker-controlled receipt chain to crash a node via RPC transaction-status queries - (File: chain/chain/src/chain.rs)

### Summary
`Chain::get_recursive_transaction_results` (called by `Chain::get_final_transaction_result`, the backend of the `tx`/`EXPERIMENTAL_tx_status` RPC methods) walks the tree of execution outcomes for a transaction using plain, unbounded Rust recursion, with no depth limit or iterative rewrite, unlike other trie-traversal code in the same codebase that was deliberately converted to iterative/queue-based traversal specifically to avoid stack overflows (e.g. `trie_recording.rs::get_subtree_size` explicitly comments "Non recursive approach to avoid any potential stack overflows"). This is analogous to the pypdf outline-traversal bug class: a tree/DAG-shaped, attacker-influenced structure is walked with naive recursion with no bound on depth, leading to large stack usage/resource consumption that a caller can trigger with a single crafted input.

### Finding Description
`get_recursive_transaction_results` recurses once per receipt produced along a transaction's receipt chain: [1](#0-0) 

Each call fetches the outcome for a receipt id, pushes it into `outcomes`, and then recurses into every `receipt_ids` entry it produced. Because a `FunctionCall` action can chain arbitrarily many self-calls via `promise_then`/`promise_batch_create` (this is exactly the pattern implemented by the test contract's `max_self_recursion_delay`, which recurses "for as long as possible through self cross-contract calls" until available gas drops below a small threshold), a single transaction with a large prepaid gas budget can generate a very long linear chain of receipts: [2](#0-1) 

Once such receipts finish executing and their outcomes are committed to the chain store, any RPC caller — not necessarily the transaction's own signer — can request the final result of that transaction, which invokes `get_final_transaction_result`: [3](#0-2) 

This walks the entire chain depth-first with one native stack frame per receipt in the chain. There is no maximum-depth guard, no per-call gas/complexity budget, and no conversion to an explicit iterative worklist (in contrast to the other trie/subtree traversal functions in the codebase that were rewritten iteratively for this exact reason). A sufficiently long receipt chain (bounded only by the transaction's prepaid gas divided by the minimal per-hop cost, which can be very large for a 300 Tgas transaction using minimal-gas hops) can therefore drive the recursion deep enough to overflow the thread stack. A Rust stack overflow aborts the process; it cannot be caught by `panic::catch_unwind`, so any node process that serves this RPC call (validators frequently also serve RPC) can be crashed by a single unprivileged RPC request following a single unprivileged transaction submission.

### Impact Explanation
A stack overflow triggered by an ordinary RPC caller querying transaction status crashes the serving node process outright. If the node offering the RPC endpoint is a validator (a common deployment pattern), this is a transaction-triggered halt of that validator's ability to produce/validate blocks until restarted, and is trivially repeatable against any node exposing this JSON-RPC method, since the crafted transaction and the subsequent status query are both unprivileged operations available to any signer/RPC caller.

### Likelihood Explanation
Likelihood is high for the RPC-DoS component: creating a long, cheap receipt chain via repeated self `promise_then` calls is a documented pattern already present in the test-contract fixtures, and querying transaction status is a completely standard, unprivileged RPC call. The only uncertainty is the exact number of hops needed to overflow a given thread's stack size versus the number of hops obtainable within the protocol's gas limits (300 Tgas per transaction, chained across possibly many blocks via `max_self_recursion_delay`-style self-calls) — this depends on runtime stack size configuration and could not be fully confirmed from static analysis alone.

### Recommendation
Rewrite `get_recursive_transaction_results` to use an explicit iterative worklist/queue (as already done in `core/store/src/trie/trie_recording.rs::get_subtree_size`) instead of native recursion, and/or impose a hard cap on the number of receipts/depth traversed per `tx`/`EXPERIMENTAL_tx_status` request, returning an error or partial result once the cap is exceeded rather than recursing without bound.

### Proof of Concept
1. Deploy a contract implementing a self-recursive `promise_then` chain equivalent to `max_self_recursion_delay` (`runtime/near-test-contracts/test-contract-rs/src/lib.rs:887-921`), sized to burn a small fixed amount of gas per hop out of a 300 Tgas budget so that many thousands of chained receipts are produced.
2. Submit a transaction invoking this method with maximum prepaid gas.
3. Once all chained receipts have executed and outcomes are persisted, call the `tx` (or `EXPERIMENTAL_tx_status`) JSON-RPC method for that transaction hash against a node serving RPC.
4. `Chain::get_final_transaction_result` → `get_recursive_transaction_results` recurses once per receipt in the chain; with a sufficiently long chain the recursive call stack exceeds the thread's stack size, aborting the serving process.

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

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L887-921)
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
