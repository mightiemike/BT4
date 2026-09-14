### Title
Unbounded native-stack recursion in transaction/receipt outcome tree assembly reachable via the `tx` / `broadcast_tx_commit` RPC endpoints - (File: chain/chain/src/chain.rs)

### Summary
`get_recursive_transaction_results` in `chain/chain/src/chain.rs` walks the tree of execution outcomes for a transaction using genuine Rust function-call recursion, with no depth limit. It is the backing implementation for both `get_final_transaction_result` and `get_partial_transaction_result_option`, which are what the `tx` / `EXPERIMENTAL_tx_status` / `broadcast_tx_commit` JSON-RPC methods ultimately call to answer "what happened to this transaction". An attacker who can extend a single transaction's receipt/outcome DAG to enough depth can crash any node whose RPC/view-client thread later tries to assemble that transaction's result, via native stack exhaustion — the exact bug class described in JLSEC-2026-23 (unbounded recursive descent over attacker-influenced structure causing stack consumption and crash), just moved from a YAML parser to a receipt-outcome walker.

### Finding Description [1](#0-0) 

`get_recursive_transaction_results` recursively calls itself once per `receipt_id` found in each outcome's `receipt_ids`, with no depth counter, no iterative rewrite, and no bound check:

```
fn get_recursive_transaction_results(&self, outcomes, id, require_all_outcomes) {
    let outcome = self.get_execution_outcome(id)?;
    outcomes.push(...);
    for idx in 0..outcomes[outcome_idx].outcome.receipt_ids.len() {
        self.get_recursive_transaction_results(outcomes, &id, require_all_outcomes)?; // recursion
    }
}
```

This is called from:
- `get_final_transaction_result` [2](#0-1) 
- `get_partial_transaction_result_option` [3](#0-2) 

both of which back the `tx`/RPC "get transaction status" family of calls used by any RPC caller.

The codebase demonstrates that engineers are aware unbounded receipt-chain traversal is dangerous and have already fixed the analogous problem elsewhere: the `ReceiptToTx` reverse lookup was rewritten with an explicit `MAX_DEPTH = 1000` and a `DepthExceeded` error [4](#0-3) , and the trie-recording subtree walker was explicitly converted from recursive to iterative "to avoid any potential stack overflows" [5](#0-4) . `get_recursive_transaction_results` received no equivalent treatment.

Critically, the depth of a transaction's outcome DAG is not bounded by that transaction's own prepaid gas. The `Yield/Resume` mechanism lets a receipt of the original transaction's tree remain pending (a `PromiseYield` receipt) until a *separate, independently-funded* transaction submits the matching `PromiseResume` data receipt [6](#0-5) , and [7](#0-6) . Because the callback executed upon resume can itself immediately create another `yield_create`, an attacker can chain: `yield_create → (external resume tx, fresh gas) → callback executes → yield_create again → ...`, indefinitely extending the *same original transaction's* outcome tree one level at a time, entirely funded transaction-by-transaction rather than by a single gas budget. Each such extension is a legitimate on-chain state transition; nothing in `apply_action_receipt`'s yield/resume handling [8](#0-7)  caps how many times this can repeat, other than the per-yield timeout (`yield_timeout_length_in_blocks`), which only bounds each individual hop's *lifetime*, not the total *chain length* the attacker can build over time.

### Impact Explanation
When any node subsequently answers a `tx` / `broadcast_tx_commit` / `EXPERIMENTAL_tx_status` RPC query for the original transaction hash, `get_recursive_transaction_results` recurses to the full depth of the attacker-built chain. Because this is genuine native call-stack recursion (not the metered/instrumented WASM stack that NEAR already guards against, see `finite_wasm_stack`/`max_stack_height` in `core/parameters/src/vm.rs`), there is no gas-based or protocol-level circuit breaker: a sufficiently long chain will exhaust the OS thread stack and crash the process handling the RPC/view-client work, i.e., a transaction-triggered halt of the RPC-serving process on any node an attacker directs a status query at (potentially every validator/RPC node in the network, since transaction results are queryable network-wide). This matches the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
The attacker only needs: (1) a contract that calls `yield_create` and, in its resume callback, immediately calls `yield_create` again; (2) the ability to submit ordinary, cheaply-funded transactions repeatedly to resume the chain. Both are available to any unprivileged transaction signer / contract deployer. No validator or network-layer privilege is required — only patience/transaction volume to build a chain deep enough to exceed a thread's stack size, after which any RPC query for that transaction's status triggers the crash. This is a purely single-account, transaction/RPC-reachable path, consistent with the required threat model.

### Recommendation
Rewrite `get_recursive_transaction_results` as an iterative traversal (e.g., BFS/DFS using an explicit `Vec`/`VecDeque` work-list, mirroring the fix already applied to `trie_recording.rs`'s `get_subtree_size`), or impose an explicit maximum traversal depth/outcome count with a `DepthExceeded`-style error, consistent with the guard already added for the `ReceiptToTx` reverse lookup (`MAX_DEPTH = 1000`). Additionally, consider bounding how many times a single transaction's outcome tree can be extended via repeated yield/resume cycles, independent of the RPC-layer fix, so the underlying DAG itself cannot grow unboundedly.

### Proof of Concept
1. Deploy a contract whose method `A` does: `promise_yield_create(callback = "A", ...)`.
2. Submit the initial transaction calling `A` — this creates a `PromiseYield` receipt as a child of the transaction's outcome tree.
3. Repeat N times (N large, e.g. tens/hundreds of thousands, spread over blocks/epochs, bounded only by `yield_timeout_length_in_blocks` per hop, which is renewed each time): submit a `promise_yield_resume` transaction that supplies the data for the pending yield, causing the callback `A` to execute and immediately create a new `PromiseYield` receipt, extending the same original transaction's execution-outcome DAG by one more level.
4. After building a sufficiently deep chain, issue a `tx` (or `EXPERIMENTAL_tx_status`/`broadcast_tx_commit`) JSON-RPC request for the original transaction hash against any node.
5. That node's call into `Chain::get_final_transaction_result` / `get_partial_transaction_result_option` recurses via `get_recursive_transaction_results` to depth N, exhausting the native stack and crashing the RPC/view-client thread/process.

(Note: exact N required to overflow a given thread's default stack size was not empirically measured here — full end-to-end confirmation of the trigger depth requires running/instrumenting the code, which is out of scope for static analysis. The recursive, unbounded nature of the code path itself is confirmed directly by the code cited above.)

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

**File:** runtime/runtime/AGENTS.md (L58-58)
```markdown
Yield/Resume is a feature which allows to `yield` and create a new receipt with an unsatisfied data dependency. Later another transaction can call `resume` with some payload to satisfy the data dependency, continuing execution of the yielded receipt. If the receipt is not resumed in time, it will time out and the yielded receipt will be executed with a timeout (None payload).
```

**File:** runtime/runtime/src/ext.rs (L405-429)
```rust
    fn submit_promise_resume_data(
        &mut self,
        data_id: CryptoHash,
        data: Vec<u8>,
    ) -> Result<bool, VMLogicError> {
        let has_yield_receipt_in_state =
            has_promise_yield_receipt(self.trie_update, self.account_id.clone(), data_id)
                .map_err(wrap_storage_error)?;
        let has_yield_status_in_state =
            has_promise_yield_status(self.trie_update, &self.account_id, data_id)
                .map_err(wrap_storage_error)?;

        if has_yield_receipt_in_state || has_yield_status_in_state {
            self.receipt_manager.create_promise_resume_receipt(data_id, data);
            set_promise_yield_status(
                &mut self.trie_update,
                &self.account_id,
                data_id,
                PromiseYieldStatus::ResumeInitiated,
            );
            return Ok(true);
        }

        Ok(false)
    }
```

**File:** runtime/runtime/src/lib.rs (L1549-1623)
```rust
            VersionedReceiptEnum::PromiseYield(_) => {
                // Received a new PromiseYield receipt. We simply store it and await
                // the corresponding PromiseResume receipt.
                set_promise_yield_receipt(state_update, receipt);
            }
            VersionedReceiptEnum::PromiseResume(data_receipt) => {
                if data_receipt.data.is_none() {
                    // This is a timeout resume. Check the status to see if the receipt has been resumed.
                    let status =
                        get_promise_yield_status(state_update, account_id, data_receipt.data_id)?;
                    if status == Some(PromiseYieldStatus::ResumeInitiated) {
                        // A non-timeout resume receipt has been sent, cancel the timeout.
                        return Ok(None);
                    }
                }

                // Received a new PromiseResume receipt delivering input data for a PromiseYield.
                // It is guaranteed that the PromiseYield has exactly one input data dependency
                // and that it arrives first, so we can simply find and execute it.
                if let Some(yield_receipt) =
                    get_promise_yield_receipt(state_update, account_id, data_receipt.data_id)?
                {
                    // Remove the receipt from the state
                    remove_promise_yield_receipt(state_update, account_id, data_receipt.data_id);

                    // Clear the PromiseYield status
                    remove_promise_yield_status(state_update, account_id, data_receipt.data_id);

                    // Clean up yield_id <-> data_id mappings if this was created by yield_create_with_id
                    if ProtocolFeature::YieldWithId.enabled(apply_state.current_protocol_version) {
                        if let Some(yield_id) = get_yield_id_for_data_id(
                            state_update,
                            account_id,
                            data_receipt.data_id,
                        )? {
                            remove_yield_id_mappings(
                                state_update,
                                account_id,
                                yield_id,
                                data_receipt.data_id,
                            );
                        }
                    }

                    // Save the data into the state keyed by the data_id
                    set_received_data(
                        state_update,
                        account_id.clone(),
                        data_receipt.data_id,
                        &ReceivedData { data: data_receipt.data.clone() },
                    );

                    // Execute the PromiseYield receipt. It will read the input data and clean it
                    // up from the state.
                    return self
                        .apply_action_receipt(
                            state_update,
                            apply_state,
                            pipeline_manager,
                            &yield_receipt,
                            receipt_sink,
                            instant_receipts,
                            validator_proposals,
                            stats,
                            epoch_info_provider,
                            receipt_to_tx,
                        )
                        .map(Some);
                } else {
                    // If the user happens to call `promise_yield_resume` multiple times, it may so
                    // happen that multiple PromiseResume receipts are delivered. We can safely
                    // ignore all but the first.
                    return Ok(None);
                }
            }
```
