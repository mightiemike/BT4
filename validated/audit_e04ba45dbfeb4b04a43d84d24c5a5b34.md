### Title
Unbounded recursion in `Chain::get_recursive_transaction_results` allows a single attacker-built receipt chain to crash an RPC/view node via stack overflow - (File: chain/chain/src/chain.rs)

### Summary
`Chain::get_final_transaction_result` walks the DAG of execution outcomes reachable from a transaction hash using a function-call-recursive helper, `get_recursive_transaction_results`, with no depth limit or iterative fallback. [1](#0-0) [2](#0-1) 

### Finding Description
`get_recursive_transaction_results` recurses once per hop in the receipt chain spawned (directly or transitively) by a transaction: for every outcome it looks at `outcome.receipt_ids` and recurses into each one before returning. [3](#0-2) 
This is structurally the same bug class as CVE-2017-9209 in QPDF's `parseInternal`: an attacker-controlled, chain-shaped/graph-shaped input is walked with native-stack recursion and no explicit depth bound, so sufficiently long chains overflow the thread stack instead of erroring gracefully.

An unprivileged account can build an arbitrarily long receipt chain over time: a contract that, on each `FunctionCall`, issues exactly one outgoing action/data receipt to itself (or another contract under the attacker's control) forms a linear chain. Each hop only needs to fit inside one chunk's gas/receipt limits (which is trivial for a single receipt), and the chain can be extended block by block with no ceiling on total chain length recorded on-chain — this is exactly the recursion mechanism already demonstrated safe *within a single call* by the existing `max_self_recursion_delay`/`recurse` test contracts, except those are bounded by gas in one execution; the RPC-side walk here is bounded only by however many blocks the attacker is willing to spend building the chain. [4](#0-3) 

Once the chain exists, any caller (not just the tx signer) can trigger the recursive walk by querying transaction status. `Chain::get_final_transaction_result` is the terminal function called with `require_all_outcomes = true`; it is invoked from JSON-RPC transaction-status paths (`tx_status_fetch` → `TxStatus` message handled by `ViewClientActor::get_tx_status`, and directly by node code such as `TransactionRunner::poll` and various test/monitoring utilities) as well as by any RPC or internal consumer polling `tx`/`EXPERIMENTAL_tx_status`. [5](#0-4) [6](#0-5) [7](#0-6) 

There is no depth-limiting equivalent to the deliberate anti-recursion mitigations that NEAR built for other recursive structures in the same codebase — e.g., `NonDelegateAction`'s custom Borsh deserializer explicitly rejects nested `DelegateAction`/`DelegateActionV2` "to avoid the recursion when Action/DelegateAction is deserialized" [8](#0-7) [9](#0-8) 
and the WASM `prepare`/`instrument` pipeline enforces `max_blocks_per_function`/`max_blocks_per_contract`/`max_stack_height` to bound recursive structures in contract code before execution. [10](#0-9) 
No analogous guard exists for the receipt-outcome tree walked by `get_recursive_transaction_results`.

The duplicate implementation in the test/integration harness (`RuntimeUser::get_recursive_transaction_results`) confirms the same unbounded-recursion pattern exists in more than one place in the codebase, underscoring this is a structural gap rather than a one-off. [11](#0-10) 

### Impact Explanation
A successful stack overflow in the process handling `get_final_transaction_result` aborts the whole node process (Rust has no way to gracefully recover from native stack exhaustion). Since this code runs inside the `Chain`/`ViewClientActor` used by RPC nodes (and, per `TransactionRunner::poll`, potentially other client-actor call sites), an attacker-triggered abort here is a transaction/query-triggered denial of service against the RPC/view-serving node: a single crafted receipt chain, queried once via a standard `tx_status`-style RPC call, can crash the serving process. This falls within the accepted impact category of "transaction-triggered halt."

### Likelihood Explanation
Reachability requires no special privileges: any account can submit ordinary `FunctionCall` transactions/receipts that self-chain (predecessor calls itself or a peer contract, producing one outgoing receipt per hop), and any RPC caller (not necessarily the original signer) can subsequently request the transaction's status to trigger the recursive walk. The only cost to the attacker is the gas/fees for producing the chain over successive blocks, which is inexpensive relative to the DoS potential, and the query itself is a normal, cheap RPC call.

### Recommendation
Convert `get_recursive_transaction_results` (and the duplicate in `RuntimeUser`) to an explicit iterative worklist/queue-based traversal (BFS/DFS with a `Vec`/`VecDeque` used as a stack instead of the call stack), removing the native recursion entirely. Additionally, consider bounding the total number of outcomes visited/returned per request to guard against excessive memory/CPU usage regardless of traversal strategy, and add a regression test that builds and queries a very long single-branch receipt chain to confirm the traversal no longer risks stack exhaustion.

### Proof of Concept
1. Deploy a contract whose exported method, on every invocation, issues exactly one outgoing `FunctionCall` promise back to itself (or to a peer contract under attacker control), forming a strictly linear receipt chain.
2. Submit an initial transaction invoking this method, then repeatedly (across many blocks) allow/trigger successive self-calls so the on-chain receipt chain grows to many thousands of hops, all reachable from the original transaction hash via `outcome.receipt_ids`.
3. Once the chain has fully executed and its outcomes are stored, call the standard transaction-status RPC (`tx`/`EXPERIMENTAL_tx_status`, which ultimately invokes `Chain::get_final_transaction_result`) for the original transaction hash from any client.
4. The RPC/view-serving node recurses once per hop inside `get_recursive_transaction_results`; with a sufficiently long chain this exhausts the native call stack and aborts the process, denying service to that node.

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

**File:** chain/client/src/view_client_actor.rs (L674-716)
```rust
    fn get_tx_status(
        &self,
        tx_hash: CryptoHash,
        signer_account_id: AccountId,
        fetch_receipt: bool,
    ) -> Result<TxStatusOutcome, TxStatusError> {
        {
            // TODO(telezhnaya): take into account `fetch_receipt()`
            // https://github.com/near/nearcore/issues/9545
            let mut request_manager = self.request_manager.write();
            if let Some(res) = request_manager.tx_status_response.pop(&tx_hash) {
                request_manager.tx_status_requests.pop(&tx_hash);
                let status = self.get_tx_execution_status(&res)?;
                let execution_outcome =
                    Some(FinalExecutionOutcomeViewEnum::FinalExecutionOutcome(res));
                return Ok(TxStatusOutcome::Observed(Box::new(TxStatusView {
                    execution_outcome,
                    status,
                })));
            }
        }

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

**File:** chain/jsonrpc/src/lib.rs (L1012-1054)
```rust
    /// Return status of the given transaction
    ///
    /// `finality` forces the execution to wait until the desired finality level is reached
    async fn tx_status_fetch(
        &self,
        tx_info: near_jsonrpc_primitives::types::transactions::TransactionInfo,
        finality: near_primitives::views::TxExecutionStatus,
        fetch_receipt: bool,
    ) -> Result<
        near_jsonrpc_primitives::types::transactions::RpcTransactionResponse,
        near_jsonrpc_primitives::types::transactions::RpcTransactionError,
    > {
        // If the request times out before any poll completes we report that we never got a
        // usable status; each poll that keeps us waiting replaces this with a better cause.
        let mut timeout_error_cause = TimeoutErrorCause::default();

        let poll_tx_status = async {
            // Create a new watch::Receiver to watch for new blocks. Mark the current block as seen.
            let mut new_block_watcher = self.block_notification_watcher.clone();
            new_block_watcher.mark_unchanged();

            loop {
                match self.tx_status_fetch_single(&tx_info, &finality, fetch_receipt).await {
                    ControlFlow::Break(outcome) => break outcome,
                    ControlFlow::Continue(cause) => timeout_error_cause = cause,
                }
                new_block_watcher.changed().await.map_err(|_| {
                    RpcTransactionError::InternalError {
                        debug_info: "block notification channel closed".to_string(),
                    }
                })?;
            }
        };

        // The polling loop returns on its own once it reaches the requested finality or hits a
        // definitive error; only a timeout falls through to `unwrap_or_else`.
        self.clock
            .timeout(self.polling_config.polling_timeout, poll_tx_status)
            .await
            .unwrap_or_else(|_| {
                self.tx_status_on_timeout(&tx_info, fetch_receipt, timeout_error_cause)
            })
    }
```

**File:** test-loop-tests/src/utils/transactions.rs (L408-414)
```rust
        if let Ok(final_res) =
            client.chain.get_final_transaction_result(&self.transaction.get_hash())
        {
            // Transaction execution is finished, save and return the final result.
            self.final_result = Some(Ok(final_res.clone()));
            return Poll::Ready(Ok(final_res));
        }
```

**File:** core/primitives/src/action/delegate.rs (L360-371)
```rust
/// This is Action which mustn't contain DelegateAction.
///
/// This struct is needed to avoid the recursion when Action/DelegateAction is deserialized.
///
/// Important: Don't make the inner Action public, this must only be constructed
/// through the correct interface that ensures the inner Action is actually not
/// a delegate action. That would break an assumption of this type, which we use
/// in several places. For example, borsh de-/serialization relies on it. If the
/// invariant is broken, we may end up with a `Transaction` or `Receipt` that we
/// can serialize but deserializing it back causes a parsing error.
#[derive(Serialize, BorshSerialize, Deserialize, PartialEq, Eq, Clone, Debug, ProtocolSchema)]
pub struct NonDelegateAction(Action);
```

**File:** core/primitives/src/action/delegate.rs (L433-443)
```rust
    impl borsh::de::BorshDeserialize for NonDelegateAction {
        fn deserialize_reader<R: Read>(rd: &mut R) -> ::core::result::Result<Self, Error> {
            match u8::deserialize_reader(rd)? {
                n if DELEGATE_VARIANT_NUMBERS.contains(&n) => Err(Error::new(
                    ErrorKind::InvalidInput,
                    "DelegateAction mustn't contain a nested one",
                )),
                n => borsh::de::EnumExt::deserialize_variant(rd, n).map(Self),
            }
        }
    }
```

**File:** runtime/near-vm-runner/src/prepare/prepare_v3.rs (L402-449)
```rust
pub(crate) fn prepare_contract(
    original_code: &[u8],
    features: crate::features::WasmFeatures,
    config: &Config,
    kind: VMKind,
) -> Result<Vec<u8>, PrepareError> {
    let lightly_steamed = PrepareContext::new(original_code, features, config).run()?;

    let analysis = finite_wasm_6::Analysis::new()
        .with_stack(SimpleMaxStackCfg)
        .with_gas(SimpleGasCostCfg {
            regular: u64::from(config.regular_op_cost),
            linear_base: config.linear_op_base_cost,
            linear_unit: config.linear_op_unit_cost,
        })
        .analyze(&lightly_steamed)
        .map_err(|err| {
            tracing::error!(target: "vm", ?err, ?kind, "analysis failed");
            PrepareError::Deserialization
        })?;
    // Make sure contracts can’t call the instrumentation functions via `env`.
    let res = InstrumentContext::new(
        &lightly_steamed,
        "internal",
        &analysis,
        config.regular_op_cost,
        config.limit_config.max_stack_height,
        config.limit_config.max_blocks_per_function.unwrap_or(u64::MAX),
        config.limit_config.max_blocks_per_contract.unwrap_or(u64::MAX),
        config.limit_config.max_params_per_function.unwrap_or(u64::MAX),
        config.limit_config.max_params_per_contract.unwrap_or(u64::MAX),
        config.limit_config.max_operand_stack_bytes_per_function.unwrap_or(u64::MAX),
    )
    .run()
    .map_err(|err| {
        use super::instrument_v3::Error;
        match err {
            Error::TooManyBlocksPerFunction => PrepareError::TooManyBlocksPerFunction,
            Error::TooManyBlocksPerContract => PrepareError::TooManyBlocksPerContract,
            Error::TooManyParamsPerFunction => PrepareError::TooManyParamsPerFunction,
            Error::TooManyParamsPerContract => PrepareError::TooManyParamsPerContract,
            Error::OperandStackTooLarge => PrepareError::OperandStackTooLarge,
            err => {
                tracing::error!(target: "vm", ?err, ?kind, "instrumentation failed");
                PrepareError::Serialization
            }
        }
    })?;
```

**File:** integration-tests/src/user/runtime_user.rs (L221-243)
```rust
    fn get_recursive_transaction_results(
        &self,
        hash: &CryptoHash,
    ) -> Vec<ExecutionOutcomeWithIdView> {
        let outcome = match self.get_transaction_result(hash) {
            Some(outcome) => outcome,
            None => {
                return vec![];
            }
        };

        let receipt_ids = outcome.receipt_ids.clone();
        let mut transactions = vec![ExecutionOutcomeWithIdView {
            id: *hash,
            outcome,
            proof: vec![],
            block_hash: Default::default(),
        }];
        for hash in &receipt_ids {
            transactions.extend(self.get_recursive_transaction_results(hash).into_iter());
        }
        transactions
    }
```
