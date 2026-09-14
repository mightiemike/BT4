### Title
Unbounded recursion in transaction-status assembly allows an attacker-crafted receipt chain to crash any RPC/view node - (File: `chain/chain/src/chain.rs`)

### Summary
`Chain::get_recursive_transaction_results` (`chain/chain/src/chain.rs:3190-3207`) walks a transaction's outcome/receipt DAG using native Rust recursion, one stack frame per receipt, with no depth limit. This function backs `get_final_transaction_result` (`:3211-3225`) and `get_partial_transaction_result_option` (`:3254-3280`), both of which are reachable from the public `tx` / `tx_status` / `EXPERIMENTAL_tx_status` JSON-RPC methods through `ViewClientActor::get_tx_status` (`chain/client/src/view_client_actor.rs:674`) and `JsonRpcHandler::tx_status_fetch`/`tx_status_common` (`chain/jsonrpc/src/lib.rs:1063`, `:1983`). Any unprivileged client can submit a transaction that fans out into a very long chain of cross-contract-call receipts and later trigger this recursive traversal by simply querying that transaction's status.

### Finding Description
`get_recursive_transaction_results` recurses once per receipt reachable from a transaction's outcome, following `outcome.receipt_ids` depth-first with an unbounded call stack: [1](#0-0) 

This is the same bug class as CVE-2018-15671: a recursive parser/walker with no depth cap that consumes native stack proportional to attacker-controlled input structure, leading to a stack-overflow crash (the HDF5 case parsed nested file structures; here the "file" is the receipt DAG a transaction generates on-chain).

Every action execution can chain into further receipts (e.g. `FunctionCall` → `action_function_call` creating new promises/receipts), and there is no protocol-level cap on the *depth* of a receipt chain — only a cap on total gas consumed per hop (`max_total_prepaid_gas`, `max_actions_per_receipt`, etc., defined in `core/parameters/src/vm.rs:59-139`). Because each hop only needs to spend a small, roughly fixed base-execution/base-send fee out of the initial prepaid gas budget, a contract that repeatedly re-invokes itself (or a chain of trivial contracts) with minimal per-hop work can turn one `max_total_prepaid_gas` budget into a receipt chain many times longer than existing tests exercise (the `max_self_recursion_delay` test in `integration-tests/src/tests/runtime/test_evil_contracts.rs:91-131` already demonstrates chains of 56–221 hops for a gas-heavier workload; a minimal-cost variant scales that number up substantially since the guardrail is a gas budget, not a hop-count limit).

Once these receipts are applied and their outcomes committed to the chain store, *any* unauthenticated caller can trigger the vulnerable recursive walk simply by querying the transaction's status:
- `chain/jsonrpc/src/lib.rs:1063` `tx_status_fetch_single` → `ViewClientActor` `TxStatus` handler
- `chain/client/src/view_client_actor.rs:674-728` `get_tx_status` → `self.chain.get_partial_transaction_result_option(&tx_hash)`
- `chain/chain/src/chain.rs:3254-3280` → `get_recursive_transaction_results`

The same pattern is duplicated (with the same unbounded recursion) in `integration-tests/src/user/runtime_user.rs:221-243`, confirming this recursive-walk design is used elsewhere too, though that copy is test-only.

By contrast, other places in the codebase that walk similarly attacker-influenced graph/tree structures have been explicitly hardened against this exact class of bug — e.g. trie subtree-size computation was rewritten to an iterative queue "to avoid any potential stack overflows" (`core/store/src/trie/trie_recording.rs:302-304`), and `NonDelegateAction`'s custom Borsh deserializer rejects nested delegate actions to bound meta-transaction recursion depth (`core/primitives/src/action/delegate.rs:433-443`). `get_recursive_transaction_results` has no analogous protection.

### Impact Explanation
A stack overflow in this function aborts the process (Rust has no way to safely catch a stack overflow — it is UB/`SIGSEGV`/abort, not a catchable panic). Since `ViewClientActor` runs as a shared actor inside the node process alongside `ClientActor` (consensus-relevant) per `chain/jsonrpc/RPC_ARCHITECTURE.md:212-231`, crashing it via a crafted `tx`/`tx_status` RPC query is a transaction-triggered halt of that node process, satisfying the "transaction-triggered halt" acceptance criterion. Because the same code path exists on every RPC node that tracks the relevant shard (any node can independently execute `get_tx_status`), an attacker can broadcast one poison transaction and then hit many different RPC/full nodes' `tx`/`tx_status` endpoints to crash them, degrading RPC availability network-wide without needing any validator or peer privileges.

### Likelihood Explanation
Likelihood is Medium: constructing the deep receipt chain requires only standard `FunctionCall` actions available to any signer/contract deployer (no privileged variables), and querying `tx_status` is a completely open, unauthenticated RPC call. The main uncertainty is the exact number of receipt hops needed to exhaust a real thread's stack (this depends on frame size of `get_recursive_transaction_results` and the runtime's stack size for the actor thread) — I was not able to empirically determine this threshold from static analysis alone, but the existing `max_self_recursion_delay` test already demonstrates chains in the hundreds of hops are trivially reachable, and the guardrail limiting chain length is a gas budget rather than a hop-count cap, so pushing into the thousands-of-hops range (typically sufficient to exhaust default 1–8MB stacks) appears achievable by minimizing per-hop gas cost.

### Recommendation
- Convert `get_recursive_transaction_results` to an iterative traversal using an explicit work queue/stack (mirroring the fix already applied in `core/store/src/trie/trie_recording.rs::get_subtree_size`), or
- Impose an explicit maximum traversal depth/outcome count and return a structured error (e.g. reuse `DBNotFoundErr`-style error or a new `Error` variant) once exceeded, rather than recursing without bound.
- Apply the same fix to the duplicate implementation in `integration-tests/src/user/runtime_user.rs` for consistency, even though that copy is test-only.

### Proof of Concept
Conceptual reproduction (cannot be executed without a running node, but is fully derivable from the code paths above):
1. Deploy a minimal contract with a method `hop(remaining_gas_marker)` that, if not at the terminal step, issues a `Promise::new(self_or_next_account).function_call("hop", ..., minimal_gas, 0)` — i.e., a cheap self- or chained-cross-contract call, analogous to `internal_recurse`/`max_self_recursion_delay` in `integration-tests/src/tests/runtime/test_evil_contracts.rs:91-156`, but with per-hop gas minimized instead of doing real work.
2. Submit one transaction invoking `hop` with `gas = max_total_prepaid_gas` (or the maximum allowed by config, see `core/parameters/src/vm.rs:59-91`); the resulting execution produces a very long linear chain of receipts, each referencing the next via `SuccessReceiptId`.
3. Once the chain has executed (or even partially, since `get_partial_transaction_result_option` is used for in-progress queries), call the public `tx` or `tx_status` JSON-RPC method for that transaction hash against any RPC node tracking the relevant shard.
4. `ViewClientActor::get_tx_status` → `Chain::get_partial_transaction_result_option`/`get_final_transaction_result` → `get_recursive_transaction_results` recurses once per receipt in the chain; with a sufficiently long chain this exhausts the actor thread's stack and crashes the node process.

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
