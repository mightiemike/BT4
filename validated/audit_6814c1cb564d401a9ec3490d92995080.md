This confirms the finding: `Chain::get_recursive_transaction_results` at `chain/chain/src/chain.rs:3190-3207` recurses once per receipt with **no depth bound**, unlike the sibling `GetReceiptToTx` handler which the developers explicitly hardened with a `MAX_DEPTH=1000` check (`chain/client/src/view_client_actor.rs`, `chain/jsonrpc-primitives/src/types/receipts.rs:102-103` `DepthExceeded`). This is reachable by any RPC caller via `tx_status`/`EXPERIMENTAL_tx_status` on any node tracking the shard (`get_tx_status` in `chain/client/src/view_client_actor.rs:674-728` → `Chain::get_partial_transaction_result_option`/`get_final_transaction_result`), and the receipt-chain depth is attacker-controlled by submitting a self-recursive cross-contract-call transaction (as demonstrated by the `max_self_recursion_delay`/`recurse` test contracts in `runtime/near-test-contracts/test-contract-rs/src/lib.rs:857-921` and `integration-tests/src/tests/runtime/test_evil_contracts.rs:133-156`), bounded only by gas, not by an explicit depth cap.

### Title
Unbounded native-stack recursion in `Chain::get_recursive_transaction_results` enables RPC-triggered stack overflow via a deep receipt chain - (File: `chain/chain/src/chain.rs`)

### Summary
`Chain::get_recursive_transaction_results` (`chain/chain/src/chain.rs:3190-3207`) walks a transaction's receipt DAG by recursively calling itself once per `receipt_ids` entry, with no depth limit. It backs the public `get_final_transaction_result` and `get_partial_transaction_result_option` methods, which are invoked by every node's `tx_status`/`EXPERIMENTAL_tx_status` JSON-RPC handler (`get_tx_status`, `chain/client/src/view_client_actor.rs:674-728`) for any transaction hash an unprivileged RPC caller supplies. The depth of the recursion is proportional to the length of the receipt chain produced by the original transaction, which an attacker fully controls by submitting a transaction that repeatedly creates self cross-contract-call receipts (exactly as the `max_self_recursion_delay`/`recurse` test contracts do). No cap analogous to `GetReceiptToTx`'s `MAX_DEPTH=1000` (`chain/jsonrpc-primitives/src/types/receipts.rs:102-103`, enforced in `chain/client/src/view_client_actor.rs`) exists for this code path.

### Finding Description
`get_recursive_transaction_results` is a plain Rust-native recursive function (not WASM), so it is outside all of the finite-wasm stack instrumentation, `max_stack_height`, and gas-metering protections that guard contract execution (`runtime/near-vm-runner/src/prepare/prepare_v3.rs:402-461`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs:257-284`). Those mechanisms only bound recursion *inside a single WASM call*; they do nothing to bound the *number of receipts in a chain*, which is limited only by total prepaid gas across possibly many chained function calls (as shown by `slow_test_self_delay`, reaching depth ~56-221 with 100 Tgas, and `test_evil_deep_recursion`). With `max_total_prepaid_gas` up to `1_000_000_000_000_000` (`core/parameters/res/runtime_configs/parameters.snap:235`), and low per-hop cost self-recursive calls, an attacker can construct a receipt chain far deeper than what was exercised in tests. Each recursive Rust call frame in `get_recursive_transaction_results` consumes native stack (locals, `Result`/`Vec` bookkeeping, `ExecutionOutcomeWithIdView::from` conversion, `Error` variants) that is not reclaimed until the whole chain unwinds, since the loop appends to a shared `outcomes` vector and calls itself in a `for` loop — the call is not tail-recursive.

### Impact Explanation
Once such a transaction executes and its receipt chain is persisted, **any** subsequent `tx_status` (or `EXPERIMENTAL_tx_status`) RPC query for that transaction hash on any node tracking the relevant shard will trigger the deep recursive walk. If the receipt chain is deep enough to exceed the native call stack, the OS will deliver `SIGSEGV`/abort the process — this is a Rust stack overflow, which is not catchable and crashes the entire node process (not just the RPC connection), because `ViewClientActor`/`Chain` run in the same process as chunk-production/validation logic. This is a transaction-triggered node crash reachable purely by submitting one transaction and then issuing a routine, publicly documented RPC call, matching the "transaction-triggered halt" impact class.

### Likelihood Explanation
Likelihood is high for the trigger conditions to be met by any unprivileged actor: creating a long self-recursive receipt chain requires only a single transaction with sufficient attached gas (the `max_self_recursion_delay` test contract already demonstrates the technique), and querying `tx_status` is a completely permissionless, standard RPC call that wallets and explorers issue automatically for every submitted transaction. The absence of any depth cap here — in contrast to the explicit `MAX_DEPTH` guard the team already added for the analogous `GetReceiptToTx` walker — suggests this path was overlooked rather than intentionally left unbounded.

### Recommendation
Convert `get_recursive_transaction_results` to an iterative traversal (explicit worklist/stack on the heap) instead of native recursion, and/or add an explicit depth/receipt-count limit (mirroring the `MAX_DEPTH` pattern used for `GetReceiptToTx`) that returns a graceful error (e.g., a new `Error` variant) once the traversal exceeds a safe bound, rather than allowing unbounded native stack growth.

### Proof of Concept
1. Deploy a contract exposing self-recursive cross-contract calls (e.g., the existing `max_self_recursion_delay`/`recurse` methods in `runtime/near-test-contracts/test-contract-rs/src/lib.rs:857-921`).
2. Submit a transaction invoking that method with the maximum allowed prepaid gas, so the runtime chains many thousands of self-call receipts (limited only by `max_total_prepaid_gas`, not by an explicit depth cap).
3. Once all receipts finish executing and are persisted, issue a `tx_status` (or `EXPERIMENTAL_tx_status`) JSON-RPC request for the original transaction hash against any node tracking that shard.
4. `get_tx_status` → `Chain::get_partial_transaction_result_option`/`get_final_transaction_result` → `get_recursive_transaction_results` recurses once per receipt in the chain with no depth check; with a sufficiently deep chain this exhausts the native call stack and crashes the serving node process. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** chain/client/src/view_client_actor.rs (L674-728)
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
                // The transaction is in the store (included) but has no execution outcome yet.
                Ok(None) => Ok(TxStatusOutcome::Observed(Box::new(TxStatusView {
                    execution_outcome: None,
                    status: TxExecutionStatus::Included,
                }))),
                // The transaction is not in this node's store at all.
                Err(near_chain::Error::DBNotFoundErr(_)) => Ok(TxStatusOutcome::NotObserved),
                Err(err) => {
                    tracing::warn!(target: "client", ?err, "error trying to get transaction result");
                    Err(err.into())
                }
            }
```

**File:** chain/jsonrpc-primitives/src/types/receipts.rs (L99-107)
```rust
pub enum RpcReceiptToTxError {
    #[error("Receipt with id {receipt_id} has never been observed on this node")]
    UnknownReceipt { receipt_id: CryptoHash },
    #[error("depth limit {limit} exceeded when resolving receipt {receipt_id}")]
    DepthExceeded { receipt_id: CryptoHash, limit: u32 },
    #[error("this node does not support receipt-to-tx lookup: {error_message}")]
    Unsupported { error_message: String },
    #[error("The node reached its limits. Try again later. More details: {error_message}")]
    InternalError { error_message: String },
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
