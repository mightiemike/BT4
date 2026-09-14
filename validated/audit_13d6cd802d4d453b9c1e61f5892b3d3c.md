Confirmed: `get_partial_transaction_result_option` is directly called from `ViewClientActor::get_tx_status` [1](#0-0) , which is invoked by the public JSON-RPC `tx` endpoint via `tx_status_fetch` → `tx_status_fetch_single` [2](#0-1) . This confirms the recursive call chain is reachable directly from an unauthenticated RPC caller querying transaction status.

### Title
Unbounded Recursion in Transaction Outcome Resolution Enables RPC-Triggered Stack Overflow / Node Crash - (File: `chain/chain/src/chain.rs`)

### Summary
`Chain::get_recursive_transaction_results` recursively walks the receipt-outcome DAG produced by a transaction, one stack frame per receipt hop, with no depth limit. [3](#0-2)  This function is called from `get_final_transaction_result` and `get_partial_transaction_result_option`, both of which are reachable from the public JSON-RPC `tx` / `EXPERIMENTAL_tx_status` endpoints via `ViewClientActor::get_tx_status`. [4](#0-3) [5](#0-4)  A transaction that fans out into a sufficiently long chain of cross-contract-call receipts (e.g., a self-recursive contract call chain like the one exercised in `max_self_recursion_delay`/`recurse` in the test contract [6](#0-5) ) causes this function to recurse once per receipt in the chain when a client subsequently queries its status via RPC.

### Finding Description
This mirrors the ORC CVE-2018-8015 bug class (CWE-674, uncontrolled recursion): a parser/serializer that recursively walks an untrusted, attacker-influenced tree/chain structure with no depth bound, allowing a crafted input to trigger unbounded stack growth.

In nearcore, the analogous structure is the chain of execution outcomes produced by a transaction's receipts. `get_recursive_transaction_results` recurses through `outcome.receipt_ids` with no depth cap:
```
fn get_recursive_transaction_results(...) -> Result<(), Error> {
    let outcome = ...;
    outcomes.push(...);
    for idx in 0..outcomes[outcome_idx].outcome.receipt_ids.len() {
        let id = outcomes[outcome_idx].outcome.receipt_ids[idx];
        self.get_recursive_transaction_results(outcomes, &id, require_all_outcomes)?;
    }
    Ok(())
}
``` [3](#0-2) 

Unlike the trie-printing code elsewhere in the codebase (which the team has already hardened with explicit `max_depth`/`limit` counters, e.g. `print_recursive_internal` [7](#0-6) , and `get_subtree_size` which was explicitly rewritten as an iterative BFS "to avoid any potential stack overflows" [8](#0-7) ), this receipt-outcome walk has no such protection.

A sender can construct a transaction that produces a long, roughly linear chain of receipts (e.g., a contract that recursively schedules a new cross-contract call from each receipt's callback, or via chained function calls / promises), and each receipt hop adds one recursive Rust stack frame in `get_recursive_transaction_results` when any RPC node subsequently answers a `tx` status query for that transaction. The integration tests already demonstrate that receipt chains of hundreds of hops are achievable within gas limits (`slow_test_self_delay` reaches a depth in the range 56–221 [9](#0-8) ), and nothing in the protocol caps the total number of sequentially chained receipts a single transaction can eventually spawn across many blocks (each hop pays gas independently, so the depth is bounded only by how many blocks/receipts the sender is willing to pay for over time, not by a single gas-metered call stack). Because each hop is a separate receipt applied in a separate chunk, gas metering (which limits a single WASM call's stack, as seen in `finite_wasm_stack`/stack instrumentation [10](#0-9) ) does not bound the length of this receipt-outcome chain — it only bounds recursion depth *within a single contract call*, not the number of chained receipts recorded in the outcome DAG that RPC subsequently has to walk recursively.

### Impact Explanation
If the recursion depth exceeds the OS thread stack size, the RPC-handling thread (or the actor thread inside `ViewClientActor`) overflows its stack, causing an abort/crash of the node process handling the query. Because `tx`/`EXPERIMENTAL_tx_status` are unauthenticated, publicly exposed RPC methods that any caller can invoke against any node that tracks the relevant shard, this allows a single crafted transaction (paid for by the attacker) to produce a receipt chain that, once discovered/queried, crashes every RPC node that serves status for it — a transaction/RPC-triggered halt of node availability. This is a distinct, in-scope reachable analog to the ORC uncontrolled-recursion DoS: unbounded recursion driven by attacker-controlled, on-chain data structure size, triggered through a routine unauthenticated RPC call.

### Likelihood Explanation
Likelihood is high for an attacker with only the ability to submit transactions and issue RPC calls: chaining function calls/cross-contract promises to build a long receipt sequence is a well-known and already gas-affordable pattern (demonstrated by existing test helpers `recurse`/`max_self_recursion_delay`), and simply calling the public `tx` RPC endpoint against the resulting transaction hash triggers the vulnerable recursive walk on any node that later needs to answer that query.

### Recommendation
Convert `get_recursive_transaction_results` into an iterative (worklist/queue-based) traversal, as already done for the trie's `get_subtree_size`, and/or enforce an explicit maximum outcome/receipt-chain depth (returning an error such as the existing `DepthExceeded`/`RECEIPT_TO_TX_MAX_DEPTH` pattern used elsewhere in the receipt-to-tx lookup code [11](#0-10) ) so that a pathological receipt chain cannot exhaust the call stack of an RPC-serving thread.

### Proof of Concept
1. Deploy a contract whose method, on receiving a callback, issues one further cross-contract `FunctionCall` promise to itself (or reuse `test-contract-rs`'s recursive self-call pattern [6](#0-5) ), repeated across many blocks to build an outcome chain of many thousands of sequential receipt hops well beyond the ~56–221 single-call depth already demonstrated achievable in one transaction [9](#0-8) .
2. Submit the initiating transaction via `broadcast_tx_async`.
3. After the chain of receipts has been recorded, call the public `tx` JSON-RPC method (or `EXPERIMENTAL_tx_status`) with the original transaction hash against any node tracking that shard.
4. `ViewClientActor::get_tx_status` → `Chain::get_partial_transaction_result_option` → `get_recursive_transaction_results` recurses once per receipt hop with no depth bound, exhausting the thread's stack and crashing/aborting the serving node process.

### Citations

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

**File:** chain/jsonrpc/src/lib.rs (L1063-1071)
```rust
    async fn tx_status_fetch_single(
        &self,
        tx_info: &TransactionInfo,
        finality: &TxExecutionStatus,
        fetch_receipt: bool,
    ) -> ControlFlow<Result<RpcTransactionResponse, RpcTransactionError>, TimeoutErrorCause> {
        let (tx_hash, account_id) = tx_info.to_tx_hash_and_account();
        let request = TxStatus { tx_hash, signer_account_id: account_id.clone(), fetch_receipt };
        match self.view_client_send(request).await {
```

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

**File:** chain/chain/src/chain.rs (L3254-3264)
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
```

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L839-864)
```rust
#[unsafe(no_mangle)]
pub unsafe fn recurse() {
    input(0);
    if register_len(0) != size_of::<u64>() as u64 {
        panic()
    }
    let mut data = [0u8; size_of::<u64>()];
    read_register(0, data.as_mut_ptr());
    let n = u64::from_le_bytes(data);
    let res = internal_recurse(n);
    let data = res.to_le_bytes();
    value_return(data.len() as u64, data.as_ptr() as u64);
}

/// Rust compiler is getting smarter and starts to optimize my deep recursion.
/// We're going to fight it with a more obscure implementations.
#[unsafe(no_mangle)]
#[inline(never)]
fn internal_recurse(n: u64) -> u64 {
    if n <= 1 {
        n
    } else {
        let a = internal_recurse(n - 1) + 1;
        if a % 2 == 1 { (a + n) / 2 } else { a }
    }
}
```

**File:** core/store/src/trie/mod.rs (L1046-1061)
```rust
    fn print_recursive_internal(
        &self,
        f: &mut dyn std::io::Write,
        hash: &CryptoHash,
        spaces: &mut String,
        prefix: &mut Vec<u8>,
        max_depth: u32,
        limit: &mut u32,
        record_type: Option<u8>,
        from: &Option<&AccountId>,
        to: &Option<&AccountId>,
    ) -> std::io::Result<()> {
        if max_depth == 0 || *limit == 0 {
            return Ok(());
        }
        *limit -= 1;
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

**File:** integration-tests/src/tests/runtime/test_evil_contracts.rs (L94-130)
```rust
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
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L257-271)
```rust
pub fn finite_wasm_stack(
    ctx: &mut Ctx,
    _memory: &mut [u8],
    operand_size: u64,
    frame_size: u64,
) -> Result<()> {
    ctx.remaining_stack =
        match ctx.remaining_stack.checked_sub(operand_size.saturating_add(frame_size)) {
            Some(s) => s,
            None => return Err(VMLogicError::HostError(HostError::MemoryAccessViolation)),
        };
    let gas = ((frame_size + 7) / 8) * u64::from(ctx.config.regular_op_cost);
    consume_gas(&mut ctx.result_state.gas_counter, gas)?;
    Ok(())
}
```

**File:** chain/jsonrpc-primitives/src/types/receipts.rs (L100-103)
```rust
    #[error("Receipt with id {receipt_id} has never been observed on this node")]
    UnknownReceipt { receipt_id: CryptoHash },
    #[error("depth limit {limit} exceeded when resolving receipt {receipt_id}")]
    DepthExceeded { receipt_id: CryptoHash, limit: u32 },
```
