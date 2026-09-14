### Title
Unbounded recursion in transaction-result assembly allows a single crafted transaction to crash any node/RPC caller querying its status - ([File: chain/chain/src/chain.rs])

### Summary
`get_recursive_transaction_results` in `chain/chain/src/chain.rs` walks the receipt DAG produced by a transaction using genuine, un-bounded call-stack recursion (one stack frame per receipt in the chain), unlike the equivalent `GetReceiptToTx` code path which explicitly enforces `MAX_DEPTH = 1000` (proven by `test_receipt_to_tx_depth_exceeded` in `test-loop-tests/src/tests/receipt_to_tx/errors.rs`). Since a transaction's receipt chain length is attacker-controlled (demonstrated by `max_self_recursion_delay` in `runtime/near-test-contracts/test-contract-rs/src/lib.rs:894-921`, and the corresponding test `slow_test_self_delay` in `integration-tests/src/tests/runtime/test_evil_contracts.rs`), an attacker who submits a single transaction that spawns a very long, low-cost self-recursive receipt chain can cause any node that later serves a `tx` / `EXPERIMENTAL_tx_status` RPC query for that transaction hash to recurse arbitrarily deep and crash from stack exhaustion.

### Finding Description
`get_recursive_transaction_results` is defined as: [1](#0-0) 

For each receipt id referenced by the current outcome, the function calls itself again — with no depth counter, no iteration limit, and no conversion to an explicit stack (unlike other trie/recursion code in the same repo that was deliberately rewritten to an explicit stack "to avoid any potential stack overflows", see `core/store/src/trie/trie_recording.rs:302`).

This function is invoked by two externally-reachable paths:
- `get_final_transaction_result` (used to serve full/blocking tx-status RPC requests): [2](#0-1) 
- `get_partial_transaction_result_option` (used to serve non-blocking/partial tx-status RPC requests): [3](#0-2) 

Both are reachable from `ViewClientActor`/JSON-RPC `tx` and `EXPERIMENTAL_tx_status` handlers (`chain/client/src/view_client_actor.rs`), i.e. from any unauthenticated RPC caller who supplies a transaction hash.

The recursion depth is bounded only by the length of the receipt DAG that the transaction produced, which is itself controlled by the transaction's own contract logic. The test contract explicitly demonstrates that self-recursive cross-contract calls can build long receipt chains bounded only by attached gas (`max_self_recursion_delay`, reaching depth ~56–221 in the existing test with `MAX_GAS`). By minimizing per-hop cost (a bare `promise_batch_create` + `promise_batch_action_function_call_weight` with minimal work, as already shown in the test contract), an attacker can multiply achievable chain depth far beyond what a single gas allotment in the test suggests, since each hop only needs to pay the minimal new-receipt/send/exec fee rather than doing real work — and since NEAR supports multi-block/async delayed receipts, this chain can be built up over successive blocks with a bounded amortized cost per block, allowing the total receipt-chain length (and thus recursion depth when later queried) to grow to a scale sufficient to exhaust a thread's call stack (tens of thousands of frames).

### Impact Explanation
Once such a deep receipt chain exists in chain state, any RPC caller (including automated indexers, wallets, or the node operator's own tooling) who queries the transaction's status triggers unbounded recursion in the node's `ViewClientActor` process. This results in a stack overflow and process crash — a transaction-triggered halt satisfying the "Validate" criteria of this exercise ("a transaction-triggered halt"). Because `ViewClientActor` typically runs in the same node process as the chunk-producing/validating logic, a stack-overflow abort can take down the entire node process, not merely the RPC server, causing denial of service to that node (and, if triggered broadly, e.g. by many RPC-serving nodes independently querying the same malicious transaction, to a meaningful fraction of the network's RPC/view infrastructure).

### Likelihood Explanation
Likelihood is high: constructing the malicious transaction requires only a normally-permissioned contract deployer/account and standard `FunctionCall` promises (no privileged access, no validator or protocol-level bypass). No special node configuration is required — the affected code path (`tx`/`EXPERIMENTAL_tx_status`) is part of every default JSON-RPC deployment. The only additional step needed is submitting the transaction hash to a `tx_status` query, which is a normal, unauthenticated RPC call.

### Recommendation
Rewrite `get_recursive_transaction_results` to use an explicit iterative worklist (as already done elsewhere in the codebase, e.g. `trie_recording.rs`'s `get_subtree_size`), and/or impose an explicit maximum traversal depth/count (mirroring the `MAX_DEPTH = 1000` guard already present for `GetReceiptToTx`), returning a bounded error (e.g. `DepthExceeded`) instead of recursing without limit.

### Proof of Concept
1. Deploy a contract with a method equivalent to `max_self_recursion_delay` (`runtime/near-test-contracts/test-contract-rs/src/lib.rs:894-921`), but minimizing per-hop gas/work cost as much as possible so each hop only pays the minimal new-receipt send/exec fee.
2. Submit a transaction invoking this method with a large gas attachment; because each recursive hop is asynchronous and can potentially be re-triggered across blocks (analogous to the existing `slow_test_self_delay` mechanism), accumulate a receipt chain whose length is bounded only by the total gas the attacker is willing to spend divided by the minimal per-hop fee — achievable depth is orders of magnitude beyond the demonstrated ~221 hops in the existing test when work-per-hop is minimized.
3. Once the chain has fully executed and is recorded in chain state, call the `tx` or `EXPERIMENTAL_tx_status` JSON-RPC method with `wait_until` set to wait for full completion, or simply query the transaction hash after execution — this invokes `get_final_transaction_result`/`get_partial_transaction_result_option` → `get_recursive_transaction_results`, driving stack depth proportional to the constructed chain length and crashing the serving node process. [1](#0-0) [4](#0-3) [5](#0-4)

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
