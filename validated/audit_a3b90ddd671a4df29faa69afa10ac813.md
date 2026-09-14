### Title
Uncontrolled Recursion in Transaction-Result Assembly Causes RPC/Validator Node Crash via Attacker-Extendable Receipt Chains - ([File: chain/chain/src/chain.rs])

### Summary
`Chain::get_recursive_transaction_results` (`chain/chain/src/chain.rs:3190-3207`) walks the DAG of execution outcomes starting from a transaction hash by recursively following each `receipt_ids` entry, with **no depth limit and no iterative fallback**. This function is invoked by `get_final_transaction_result` (`:3211-3225`) and `get_partial_transaction_result_option` (`:3254-3280`), both of which are reachable from unprivileged JSON-RPC callers via the `tx` / `EXPERIMENTAL_tx_status` / `broadcast_tx_commit` endpoints (`chain/client/src/view_client_actor.rs:674-754`, `902-909`, `1955-1970`). A transaction whose receipt chain is deep enough (e.g. via repeated self cross-contract calls, a technique the codebase itself acknowledges and tests — see `slow_test_self_delay` / `max_self_recursion_delay` in `integration-tests/src/tests/runtime/test_evil_contracts.rs:91-131,887-921`, explicitly built to "delay the conclusion of a receipt for as long as possible through the use of self cross-contract calls") can produce an outcome chain whose length is not bounded to a single transaction's gas budget, since each hop re-arms with fresh prepaid gas across successive blocks.

### Finding Description
`get_recursive_transaction_results` is a plain unbounded Rust recursive function: [1](#0-0) 

Contrast this with the trie-walking code elsewhere in the same codebase, which explicitly avoids recursion for exactly this reason: [2](#0-1) 

No equivalent protection exists for `get_recursive_transaction_results`. Each stack frame pushes into `outcomes`, does a DB read via `get_execution_outcome`, and recurses again for every entry in `receipt_ids`. The recursion depth equals the depth of the receipt/outcome DAG reachable from the queried transaction hash — this is attacker-influenced, not attacker-limited to a single transaction's gas allowance, because a contract can keep re-issuing self-cross-contract-call receipts block after block (each new receipt gets its own fresh prepaid-gas budget), extending the SuccessReceiptId/receipt_ids chain indefinitely over time, as demonstrated by the "delay for as long as possible" test helper `max_self_recursion_delay` in `runtime/near-test-contracts/test-contract-rs/src/lib.rs:887-921`.

This function is reached by unprivileged RPC/transaction callers through:
- `ViewClientActor::get_tx_status` → `Chain::get_partial_transaction_result_option` → `get_recursive_transaction_results` (`chain/client/src/view_client_actor.rs:674-754`, handlers at `:902-909` and `:1955-1970`), backing the public `tx`/`EXPERIMENTAL_tx_status` RPC methods and `broadcast_tx_commit`.
- `Chain::get_final_transaction_result` (`chain/chain/src/chain.rs:3211-3225`), used the same way when a caller waits for full completion.

Any RPC node (or validator acting as its own view client) that receives a status query for a transaction with a sufficiently long receipt chain will recurse to that depth in a single call stack frame sequence, with no depth cap.

### Impact Explanation
Rust's `StackOverflowException`-equivalent (SIGSEGV on stack guard page) is not catchable; it aborts the whole process. An RPC node or validator's view-client thread crashing on an ordinary status query is a transaction-triggered process crash / Denial of Service, matching the "transaction-triggered halt" acceptance bar. Because the RPC/view-client subsystem is shared infrastructure serving all clients on that node, this can degrade or take down public RPC endpoints or validator nodes that serve their own status queries, without requiring any privileged access — only the ability to submit transactions and later query their status.

### Likelihood Explanation
Reaching this requires an attacker to build a sufficiently deep receipt/outcome chain and then query its status. The codebase's own test/documentation confirms that self-recursive cross-contract-call chains that persist "for as long as possible" are achievable and intentionally exercised (`slow_test_self_delay`), and such chains are not capped to the ~200 depth achieved in one block's gas budget — they can be re-armed indefinitely across blocks, since each hop is a fresh receipt with its own prepaid gas. Reaching the tens of thousands of recursive frames typically needed to exhaust an 8MB thread stack (as referenced in the analog AutoMapper report) requires sustained effort over many blocks, which raises the cost of the attack somewhat, but it is not prevented by protocol or gas limits — it is bounded only by wall-clock/blocks the attacker is willing to spend, and the resulting outcome chain persists in state and will crash any node that later queries its final status. This is a genuinely reachable, moderate-effort DoS path from an unprivileged transaction submitter, warranting Medium-High severity.

### Recommendation
Replace the recursive DFS in `get_recursive_transaction_results` with an explicit iterative worklist (`Vec`/`VecDeque`) as already done in `core/store/src/trie/trie_recording.rs::get_subtree_size` and `core/store/src/trie/state_parts.rs::traverse_all_nodes`. Additionally/alternatively, enforce a maximum traversal depth or maximum number of visited outcomes, returning an error (e.g. `TooManyReceipts`) once the bound is exceeded, so that pathological receipt chains fail gracefully instead of overflowing the stack. Apply the same fix to `get_final_transaction_result` (`chain/chain/src/chain.rs:3211`) and `get_partial_transaction_result_option` (`:3254`), and the mirrored test-only implementation in `integration-tests/src/user/runtime_user.rs:221-243` if it is ever used in a security-relevant path.

### Proof of Concept
1. Deploy a contract implementing a self cross-contract-call loop equivalent to `max_self_recursion_delay` (`runtime/near-test-contracts/test-contract-rs/src/lib.rs:893-921`), which re-issues a call to itself with `promise_batch_action_function_call_weight`, consuming attached gas and chaining `SuccessReceiptId` results.
2. Submit a transaction invoking this method, then repeatedly re-trigger the self-call chain across many blocks (each hop gets its own fresh prepaid-gas allocation), building a receipt/outcome chain whose length grows unboundedly over time rather than being capped by a single transaction's gas budget.
3. Once the chain length is large enough to exceed the thread's stack budget in `get_recursive_transaction_results`'s per-frame cost, query the original transaction's status via the public `tx` / `EXPERIMENTAL_tx_status` RPC method (or `broadcast_tx_commit`) against any RPC node tracking the relevant shard.
4. `ViewClientActor::get_tx_status` invokes `Chain::get_partial_transaction_result_option` → `get_recursive_transaction_results`, which recurses to the full chain depth and overflows the stack, crashing the RPC node process.

Note: I was unable to fully verify the exact minimum receipt-chain depth needed to trigger a real stack overflow (this depends on the per-frame stack size of `get_recursive_transaction_results`'s generated code and the runtime's configured thread stack size, neither of which I could measure via static code search), so the practical attack cost (number of blocks/receipts needed) is an estimate based on the codebase's own test comments about unbounded self-recursion chains, not a directly measured value.

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

**File:** core/store/src/trie/trie_recording.rs (L302-304)
```rust
        // Non recursive approach to avoid any potential stack overflows.
        let mut queue: VecDeque<CryptoHash> = VecDeque::new();
        queue.push_back(*subtree_root);
```
