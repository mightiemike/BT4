## Title
Unbounded native recursion in transaction-status assembly can stack-overflow the JSON-RPC/view-client process - (File: chain/chain/src/chain.rs)

### Summary
`Chain::get_recursive_transaction_results` performs a depth-first, non-tail Rust function recursion over the tree of execution outcomes reachable from a transaction hash, with **no depth limit**, and is reachable from the public `tx` / `EXPERIMENTAL_tx_status` JSON-RPC methods that any unprivileged caller can invoke for any transaction hash on chain.

### Finding Description
`get_recursive_transaction_results` walks `outcome.receipt_ids` recursively, once per generated receipt, to assemble the full outcome tree for a transaction: [1](#0-0) 

This is invoked directly by `get_final_transaction_result`: [2](#0-1) 

`get_final_transaction_result` (and the sibling partial-result path) is reached from the `ViewClientActor`'s `TxStatus` handler, which backs the public `tx` and `EXPERIMENTAL_tx_status` JSON-RPC methods documented in the RPC architecture notes and exercised by `get_tx_status`: [3](#0-2) [4](#0-3) 

Unlike this walk, an analogous recursive-graph traversal elsewhere in the codebase (`ReceiptToTx` ancestry lookup) was deliberately hardened with an explicit `MAX_DEPTH = 1000` bound, as shown by its dedicated regression test: [5](#0-4) 

No equivalent bound exists for `get_recursive_transaction_results`. A receipt-outcome chain of practically unbounded length is achievable: the test contract's `max_self_recursion_delay` method demonstrates a self-perpetuating chain of cross-contract calls (each producing one child receipt, i.e. linear "depth"), and the codebase explicitly notes the achievable depth scales with available gas and is only capped when gas runs low within one call's budget: [6](#0-5) [7](#0-6) 

While a single call's gas budget bounds depth to roughly 60–220 hops in that specific test, an attacker is not limited to one call: they can chain successive `FunctionCall` receipts (each with freshly-attached gas/deposit, funded incrementally) across many blocks, indefinitely extending the linear `receipt_ids` chain recorded in on-chain execution outcomes. Because `get_recursive_transaction_results` recurses once per hop with a real (non-tail) stack frame, and the recursion depth is bounded only by however many blocks/hops the attacker is willing to pay for — not by any protocol-enforced structural limit — a sufficiently long chain will exhaust the OS thread stack of the `ViewClientActor` (or whichever RPC-serving process handles the `tx` query), causing a stack overflow. This mirrors the CVE-2023-4155 pattern: a handler that can be driven to recurse an attacker-controlled number of times without a depth guard, culminating in a stack-overflow crash of the serving process (a Rust `SIGSEGV`/abort, since Rust panics on stack overflow abort the whole process rather than being catchable).

### Impact Explanation
A stack overflow in `ViewClientActor`'s thread aborts that process. On any RPC node (including validator nodes that also serve RPC, or full nodes relied on by wallets/indexers) this results in a transaction-triggered denial-of-service: a single crafted transaction chain, once queried via a standard `tx`/`EXPERIMENTAL_tx_status` RPC call by any client, crashes the serving node process. This satisfies the "transaction-triggered halt" impact category — an unprivileged transaction signer or RPC caller can crash a node's serving process merely by submitting a self-perpetuating call chain and then requesting its status.

### Likelihood Explanation
Constructing the receipt chain requires the attacker to pay gas for each hop across successive transactions/blocks, so building a chain deep enough to overflow a typical (multi-MB) OS thread stack requires sustained investment (potentially many blocks and moderate cumulative gas cost), but there is no protocol-level obstacle preventing it — no cap exists on total tree depth for a transaction's execution outcome, and it is fully attacker-controlled and reproducible. The only mitigating factor is the sheer number of hops needed relative to typical stack sizes, which reduces immediate practicality but does not eliminate the underlying missing-bound defect (the codebase's own precedent of adding `MAX_DEPTH` for the structurally similar `ReceiptToTx` walk indicates this class of bug was recognized as needing an explicit fix there, but the fix was not applied to this analogous recursive walk).

### Recommendation
Convert `get_recursive_transaction_results` to an iterative (explicit-stack or queue-based) traversal, or add an explicit maximum depth/outcome-count bound (mirroring the `MAX_DEPTH` pattern used for `ReceiptToTx`) that returns a graceful error instead of recursing unbounded. Apply the same fix to any other unbounded recursive outcome/receipt walkers reachable from RPC.

### Proof of Concept
1. Deploy (or reuse) a contract exposing a self-recursive cross-contract-call method such as `max_self_recursion_delay` (already present in the test contract).
2. Submit an initial transaction, then repeatedly submit follow-up transactions from the account (or let a similar self-chaining pattern run) across many blocks to extend the on-chain execution-outcome/receipt chain to many thousands of hops, well beyond the ~60–220 achievable within a single call's gas budget as observed by `slow_test_self_delay`: [8](#0-7) 
3. Once the chain is long enough, issue a standard `tx` or `EXPERIMENTAL_tx_status` JSON-RPC request for the originating transaction hash against a node tracking that shard, triggering `TxStatus` → `get_tx_status` → `get_final_transaction_result` → `get_recursive_transaction_results`: [9](#0-8) 
4. The recursive walk over the receipt-outcome chain consumes one stack frame per hop; with a sufficiently long chain, the `ViewClientActor` thread stack overflows and the serving process aborts/crashes, denying RPC service.

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

**File:** chain/chain/src/chain.rs (L3211-3225)
```rust
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

**File:** chain/client/src/view_client_actor.rs (L902-909)
```rust
impl Handler<TxStatus, Result<TxStatusOutcome, TxStatusError>> for ViewClientActor {
    fn handle(&mut self, msg: TxStatus) -> Result<TxStatusOutcome, TxStatusError> {
        tracing::debug!(target: "client", ?msg);
        let _timer =
            metrics::VIEW_CLIENT_MESSAGE_TIME.with_label_values(&["TxStatus"]).start_timer();
        self.get_tx_status(msg.tx_hash, msg.signer_account_id, msg.fetch_receipt)
    }
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
