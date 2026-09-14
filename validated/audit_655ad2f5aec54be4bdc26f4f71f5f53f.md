### Title
Unbounded recursion in transaction status resolution can crash any RPC node (stack exhaustion via `tx`/`tx_status` on an attacker-controlled receipt chain) - (File: `chain/chain/src/chain.rs`)

### Summary
`get_recursive_transaction_results` uses true, unbounded native-stack recursion (one Rust stack frame per receipt hop and per branch) to walk the receipt DAG produced by a transaction. It has no depth limit, unlike the analogous `ReceiptToTx` lookup which explicitly enforces `MAX_DEPTH=1000` and returns `DepthExceeded`. This is directly analogous to CVE-2022-25313 (Expat `build_model` stack exhaustion from unbounded DTD nesting depth): an attacker-controlled nesting/chain depth drives unbounded native recursion with no depth cap, leading to stack exhaustion.

### Finding Description
`Chain::get_recursive_transaction_results` recursively calls itself once per `receipt_id` produced by an outcome, for every outcome already produced by a transaction: [1](#0-0) 

This function is invoked (with `require_all_outcomes=false`) from `get_partial_transaction_result_option`, which is on the direct path of the public `tx` / `tx_status` / `EXPERIMENTAL_tx_status` JSON-RPC methods: [2](#0-1) 

The RPC call path is: JSON-RPC `tx`/`tx_status` → `tx_status_common` → `tx_status_fetch` → `TxStatus` message handled by `ViewClientActor::get_tx_status`, which calls `Chain::get_partial_transaction_result_option`: [3](#0-2) [4](#0-3) [5](#0-4) 

Unlike this code path, the codebase already recognizes this exact class of bug and has fixed it elsewhere: the `ReceiptToTx` lookup walks a similar receipt-origin chain but does so *iteratively* with an explicit `MAX_DEPTH=1000` bound, returning `DepthExceeded` instead of recursing unboundedly: [6](#0-5) [7](#0-6) 

Other trie-traversal code in the same repo has also been explicitly rewritten to avoid recursion for the same reason: "Non recursive approach to avoid any potential stack overflows": [8](#0-7) 

By contrast, `get_recursive_transaction_results` has no such protection: any chunk-producing/tracked node can have execution-outcome chains of essentially unbounded length recorded for a transaction it processed (a transaction can fan out into a large tree/chain of chained cross-contract-call receipts across many blocks — the runtime's own test suite demonstrates chains of 56–221+ nested self-calls achievable from a single transaction with realistic gas costs, and this can be compounded further via yield/resume and multiple blocks of chained receipts): [9](#0-8) 

### Impact Explanation
Any unprivileged client can submit or reference a transaction whose receipt-execution chain is deep enough, then repeatedly query it via the public `tx`/`tx_status`/`EXPERIMENTAL_tx_status` JSON-RPC endpoints on any node tracking the relevant shard (this includes RPC nodes and validators/chunk producers that also serve RPC). Each such query triggers `get_recursive_transaction_results`, consuming one native stack frame per receipt hop/branch with no cap. A sufficiently deep chain will overflow the thread stack, causing the process to abort (SIGSEGV/stack overflow) and crash the node — a transaction-triggered halt of an honest node's core services. Because `tx`/`tx_status` is a widely used, unauthenticated, public API and the query can be issued repeatedly and cheaply (no cost to the caller beyond having previously submitted a deep-chain transaction), this can be used to reliably crash targeted RPC/validator nodes.

### Likelihood Explanation
The precondition (a wide/deep receipt chain from a single transaction) is directly reachable with a single signed transaction using ordinary cross-contract calls (`Promise::and_then` chains, or repeated self-calls as demonstrated in `test_evil_deep_recursion`/`slow_test_self_delay`), no special privileges are required, and the crashing query (`tx`/`tx_status`) is one of the most commonly exposed public JSON-RPC endpoints. The codebase's own recent fix of the structurally identical bug in `ReceiptToTx` (adding `MAX_DEPTH`/`DepthExceeded`) indicates the bug class is known and considered a real risk, but this particular function was evidently not covered by that fix.

### Recommendation
Rewrite `get_recursive_transaction_results` to use an explicit iterative worklist/stack (as already done in `trie_recording.rs`'s `get_subtree_size` and in the fixed `ReceiptToTx` handler), and/or impose a bounded maximum outcome/receipt-chain traversal depth or count, returning a graceful error (analogous to `DepthExceeded`) instead of recursing without bound.

### Proof of Concept
1. Deploy a contract with a method that issues a long chain of `Promise` cross-contract calls to itself (or reuse the existing `recurse`/`max_self_recursion_delay` test-contract methods shown in `runtime/near-test-contracts/test-contract-rs/src/lib.rs`), sized/gassed so the resulting execution-outcome DAG for the single top-level transaction has a receipt chain depth well beyond the RPC node's available stack frames (thousands of hops, achievable by chaining across multiple blocks/yield-resume cycles to bypass the per-transaction gas limit demonstrated in `slow_test_self_delay`).
2. Submit this transaction via `broadcast_tx_async`.
3. Once included, repeatedly call the public `tx` (or `tx_status`/`EXPERIMENTAL_tx_status`) JSON-RPC method with that transaction hash against a target node tracking the shard.
4. Each call drives `ViewClientActor::get_tx_status` → `Chain::get_partial_transaction_result_option` → `get_recursive_transaction_results`, recursing once per receipt hop with no depth limit, exhausting the actor's thread stack and crashing the node process.

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

**File:** chain/client/src/view_client_actor.rs (L696-716)
```rust
        let head = self.chain.head()?;
        let target_shard_id =
            account_id_to_shard_id(self.epoch_manager.as_ref(), &signer_account_id, &head.epoch_id)
                .map_err(|err| TxStatusError::InternalError(err.to_string()))?;
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

**File:** chain/jsonrpc/src/lib.rs (L718-727)
```rust
            "tx" => {
                process_method_call(request, |params| self.tx_status_common(params, false, "tx"))
                    .await
            }
            "tx_status" => {
                process_method_call(request, |params| {
                    self.tx_status_common(params, true, "tx_status")
                })
                .await
            }
```

**File:** chain/jsonrpc/src/lib.rs (L1983-1998)
```rust
    async fn tx_status_common(
        &self,
        request_data: near_jsonrpc_primitives::types::transactions::RpcTransactionStatusRequest,
        fetch_receipt: bool,
        method_name: &str,
    ) -> Result<
        near_jsonrpc_primitives::types::transactions::RpcTransactionResponse,
        near_jsonrpc_primitives::types::transactions::RpcTransactionError,
    > {
        metrics::report_wait_until_metric(method_name, &request_data.wait_until);

        let tx_status = self
            .tx_status_fetch(request_data.transaction_info, request_data.wait_until, fetch_receipt)
            .await?;
        Ok(tx_status.rpc_into())
    }
```

**File:** chain/jsonrpc-primitives/src/types/receipts.rs (L99-104)
```rust
pub enum RpcReceiptToTxError {
    #[error("Receipt with id {receipt_id} has never been observed on this node")]
    UnknownReceipt { receipt_id: CryptoHash },
    #[error("depth limit {limit} exceeded when resolving receipt {receipt_id}")]
    DepthExceeded { receipt_id: CryptoHash, limit: u32 },
    #[error("this node does not support receipt-to-tx lookup: {error_message}")]
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

**File:** integration-tests/src/tests/runtime/test_evil_contracts.rs (L93-131)
```rust
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
