### Title
Unbounded recursive DFS over receipt outcomes in `get_final_transaction_result` allows RPC-triggered stack-overflow crash on `tx`/`tx_status` queries - (File: chain/chain/src/chain.rs)

### Summary
`Chain::get_recursive_transaction_results` walks a transaction's outcome/receipt tree via plain (non-tail) recursion with no depth limit, no visited-set, and no iterative fallback. It is invoked by `Chain::get_final_transaction_result`, which backs the `tx` / `tx_status` / `EXPERIMENTAL_tx_status` JSON-RPC methods reachable by any unauthenticated RPC caller. A transaction that produces a sufficiently deep chain of receipts (via chained cross-contract calls / `promise_then` callbacks) causes this function to recurse one stack frame per receipt in the chain when a client later queries its status, risking a stack overflow that aborts the node process — a transaction-triggered halt reachable purely through normal RPC usage.

### Finding Description
`get_recursive_transaction_results` recurses once per `receipt_id` found in each outcome's `receipt_ids` list, with no maximum-depth guard: [1](#0-0) 

It is called directly from the RPC-facing entry point `get_final_transaction_result`: [2](#0-1) 

This is exposed to any RPC caller through the `tx` / `tx_status` handlers (`tx_status_common` → `tx_status_fetch` → `TxStatus` message → `ViewClientActor` → `Chain::get_final_transaction_result`): [3](#0-2) 

Notably, the codebase already recognizes this bug class and has patched an analogous parent-walk in `EXPERIMENTAL_receipt_to_tx` with an explicit `MAX_DEPTH = 1000` guard and regression tests proving unguarded recursive/iterative walks can spin or exceed practical limits: [4](#0-3) [5](#0-4) 

No equivalent guard exists in `get_recursive_transaction_results`. Unlike `receipt_to_tx`'s parent-pointer walk (which can self-loop and thus needed a fixed-point break), the receipt tree walked here cannot cycle back to an ancestor (each receipt ID is freshly derived per hop), but nothing bounds its *depth*: an attacker fully controls how many sequential cross-contract-call hops a single transaction's execution produces (each `promise_then`/callback adds one more receipt as a child of the previous one, consuming only the gas passed forward). Given the ~300 Tgas budget per transaction and the low relative cost of a minimal cross-contract call hop, an attacker can construct execution trees with recursion depth in the thousands purely from within a single signed transaction.

The identical unbounded-recursion pattern also exists in the equivalent client test-harness implementation, corroborating that the pattern (and its risk) is systemic, not confined to `chain.rs`: [6](#0-5) 

### Impact Explanation
Because `get_recursive_transaction_results` is plain (non-tail, non-iterative) recursion, each recursion level consumes a stack frame containing a `CryptoHash`, error-handling context, and vector index computations. A sufficiently deep receipt chain (achievable from a single attacker-controlled transaction that chains enough cross-contract-call hops) can exceed the default thread stack size when a client subsequently calls `tx`/`tx_status`/`EXPERIMENTAL_tx_status` for that transaction hash. In Rust, stack overflow triggers an immediate process abort rather than a catchable panic, so this can crash the entire node process handling the RPC request — a transaction-triggered denial of service reachable by any RPC caller (no special privileges, no validator/network position required), matching the "transaction-triggered halt" impact category.

### Likelihood Explanation
Likelihood is elevated because:
- The attacker only needs to submit one ordinary signed transaction that performs many chained cross-contract calls (a widely-used, unprivileged capability of the NEAR runtime, e.g. `promise_then` callback chains).
- Triggering the crash requires only a subsequent, unauthenticated `tx`/`tx_status` RPC call for that transaction's hash — something block explorers, wallets, and indexers do automatically for every transaction they observe.
- The codebase's own fix for an analogous unguarded-walk bug (`receipt_to_tx`'s `MAX_DEPTH`) demonstrates this class of issue was already identified as a real risk in a sibling code path but was not applied here.

### Recommendation
Add an explicit maximum-depth (or maximum total collected outcomes) guard to `get_recursive_transaction_results`, mirroring the `MAX_DEPTH` bound already used in `EXPERIMENTAL_receipt_to_tx`, and convert the traversal to an iterative worklist/stack-based algorithm (as already done elsewhere in the codebase, e.g. `TrieStorageUpdate::flatten_nodes`, `Trie::traverse_all_nodes`) so that receipt-tree depth cannot translate into native call-stack depth. Return a clear, bounded error (e.g. `DepthExceeded`) once the limit is hit instead of failing via stack exhaustion.

### Proof of Concept
1. Deploy a contract with a method that, on each invocation, issues a `promise_then` cross-contract call to itself (or another contract) with the remaining prepaid gas, forming a linear callback chain: `receipt_0 → receipt_1 → receipt_2 → … → receipt_N`.
2. Submit a single signed transaction invoking this method with the maximum allowed attached gas (up to 300 Tgas), causing the runtime to produce as many chained receipts as the gas budget allows (each hop only needs to cover minimal base + function-call fees, allowing many hundreds to low thousands of hops).
3. Once the transaction and its full receipt chain have executed across the relevant chunks, call the `tx` or `tx_status` JSON-RPC method (or `EXPERIMENTAL_tx_status`) with the original transaction hash and `wait_until` set to a level that requires the full outcome (e.g. `EXECUTED`/`FINAL`).
4. `ViewClientActor`/`Chain::get_final_transaction_result` invokes `get_recursive_transaction_results`, which recurses once per receipt in the chain; for a sufficiently long chain this exhausts the serving thread's stack and aborts the node process.

*Note: exact stack-frame size and the precise number of hops needed to overflow a given deployment's stack were not empirically measured here (would require running the binary); this PoC establishes the reachable, gas-bounded-but-attacker-controlled recursion depth and the unguarded recursive call path as the root cause.*

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

**File:** test-loop-tests/src/tests/receipt_to_tx/hint.rs (L1350-1386)
```rust
/// Resharding boundary, self-parenting shard hint: a hint naming a shard that
/// did NOT split must not hang the parent-shard walk.
///
/// `try_get_parent_shard_id` reports an unchanged shard (and a V3 non-split-child)
/// as its own parent, so the lineage walk in `resolve_scan_shards` must stop at
/// that fixed point. Without the guard it spins forever on the post-split layout;
/// with it the walk terminates and, finding no outcome, returns `UnknownReceipt`.
///
/// Regression guard: revert the `parent == id` break → this test hangs (the
/// test-loop has no wall-clock escape from a synchronous CPU loop).
#[test]
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_hint_self_parenting_shard_does_not_loop() {
    init_test_logger();

    let boundary_account: AccountId = "boundary".parse().unwrap();
    let ReshardBoundary { mut env, h_anchor, unchanged_shard_id, .. } =
        setup_reshard_boundary(ReshardKind::Static, &boundary_account);

    // Hint the unchanged shard with a window spanning the post-split layout,
    // where it is its own parent. No synthetic outcome is injected, so a
    // terminating walk must report `UnknownReceipt` rather than hang.
    let receipt_id = CryptoHash::hash_bytes(b"reshard-self-parent-absent-receipt");
    let result = handle(
        &mut env,
        GetReceiptToTx {
            receipt_id,
            block_height: Some(h_anchor),
            shard_id: Some(unchanged_shard_id),
            window: Some(5),
        },
    );
    match result {
        Err(GetReceiptToTxError::UnknownReceipt(id)) => assert_eq!(id, receipt_id),
        other => panic!("expected UnknownReceipt from a terminating walk, got {other:?}"),
    }
}
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
