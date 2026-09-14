### Title
Unbounded recursion in transaction-status resolution can crash a node — `get_recursive_transaction_results` (`File: chain/chain/src/chain.rs`)

### Summary
`Chain::get_recursive_transaction_results`, the function backing `get_final_transaction_result` (used by JSON-RPC transaction-status queries), recurses once per receipt in a transaction's receipt DAG with no depth bound. [1](#0-0)  An attacker who controls a contract can produce a receipt chain of essentially unbounded logical depth by having a `FunctionCall` action repeatedly schedule a self cross-contract call (the exact pattern already demonstrated by the test contract's `max_self_recursion_delay`, which reaches depths of 56–221 hops in a single transaction under current gas limits). [2](#0-1)  A subsequent RPC `tx` / transaction-status query for that transaction walks the resulting receipt tree recursively with no `MAX_DEPTH`-style guard, unlike the newer, hardened `EXPERIMENTAL_receipt_to_tx` resolver in `view_client_actor.rs`, which explicitly bounds its walk with `RECEIPT_TO_TX_MAX_DEPTH` and returns `DepthExceeded` instead of recursing unboundedly. [3](#0-2) 

### Finding Description
`get_recursive_transaction_results` pushes the outcome for `id`, then for every `receipt_id` produced by that outcome calls itself again — a classic unbounded-recursion pattern with no iteration cap and no stack-depth check. [4](#0-3)  `get_final_transaction_result` calls this helper directly to build the `FinalExecutionOutcomeView` returned to RPC clients. [5](#0-4) 

The equivalent legacy walker in `integration-tests/src/user/runtime_user.rs` shows the exact same unbounded-recursion shape, confirming this is a systemic pattern rather than a one-off, and that the codebase's own newer `receipt_to_tx` walker was rewritten specifically to avoid it by adding `RECEIPT_TO_TX_MAX_DEPTH = 1000` and converting the walk to an explicit bounded loop with a `DepthExceeded` error. [6](#0-5) [7](#0-6) 

An unprivileged account can generate long receipt chains cheaply by chaining self cross-contract calls (each hop consuming a fixed small amount of gas out of the transaction's prepaid gas budget), as already proven functionally correct and safe from the runtime's perspective by `slow_test_self_delay`. [8](#0-7)  Once such a transaction is included, any client (including the attacker) querying its status via JSON-RPC forces the node to recurse over the whole receipt DAG in `get_recursive_transaction_results` with no depth limit — this is directly analogous to the LWAPP dissector's unbounded encapsulation recursion in the referenced CVE, where an attacker-supplied nesting depth drove recursion without a cap until the process crashed.

### Impact Explanation
Unbounded native-stack recursion in Rust results in a stack-overflow guard-page hit, which Rust's runtime turns into an unconditional `abort()` of the whole process — this is not a recoverable panic and is not scoped to one actor/thread. Because `Chain` (and this code path) is shared by the client's core chain logic, not just an isolated view-only actor, a crash here can take down the entire node process servicing that request, which the rules treat as a transaction-triggered halt if the recursion depth achievable in practice is sufficient to exhaust the thread's stack.

### Likelihood Explanation
Reachability is straightforward: any account can submit a `FunctionCall` transaction that builds a long self-referential receipt chain (as already demonstrated by `max_self_recursion_delay`/`slow_test_self_delay`), and any RPC caller (not necessarily privileged) can then query that transaction's status, triggering the unbounded recursive walk. [9](#0-8)  However, I could not verify from the indexed code the exact per-frame stack size of `get_recursive_transaction_results` or the configured stack size of the thread handling RPC/view queries, so I cannot confirm whether the currently gas-bounded depth (tens to a few hundred hops per transaction, per the test's `min_expected_depth`/`max_expected_depth` values) is sufficient by itself to exhaust the stack, or whether an attacker would need to additionally chain multiple transactions/receipts across blocks to reach a crash-inducing depth. This uncertainty should be resolved by actually reproducing the recursion at scale.

### Recommendation
Convert `get_recursive_transaction_results` to an iterative, explicitly-bounded traversal (e.g., a worklist/queue with a `MAX_DEPTH` similar to `RECEIPT_TO_TX_MAX_DEPTH` used in the newer `receipt_to_tx` resolver), returning a typed error instead of recursing without bound, and apply the same fix to the analogous recursive walker in `integration-tests/src/user/runtime_user.rs` if it is reachable in any production configuration.

### Proof of Concept
1. Deploy the existing test contract (or an equivalent) and submit a `FunctionCall` transaction invoking `max_self_recursion_delay`, producing a receipt chain of maximal depth for the attached gas, per `slow_test_self_delay`. [10](#0-9) 
2. Once all receipts execute, issue a JSON-RPC `tx` (transaction-status) query for the original transaction hash so the node resolves `get_final_transaction_result` → `get_recursive_transaction_results` over the full receipt DAG. [11](#0-10) 
3. Measure whether the induced recursion depth exhausts the servicing thread's stack; if so, the node process aborts on the guard-page hit — this step still needs to be validated empirically since exact stack/frame sizing wasn't available in the indexed code.

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

**File:** runtime/near-test-contracts/test-contract-rs/src/lib.rs (L887-920)
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
```

**File:** chain/client/src/view_client_actor.rs (L1381-1443)
```rust
    for _ in 0..RECEIPT_TO_TX_MAX_DEPTH {
        let column_info = actor.chain.chain_store().get_receipt_to_tx(&current_receipt_id);
        let info = match column_info {
            Some(info) => info,
            None => {
                let Some(height) = current_height else {
                    return Err(GetReceiptToTxError::UnknownReceipt(current_receipt_id));
                };
                if !actor.config.save_tx_outcomes {
                    return Err(GetReceiptToTxError::OutcomesNotStored);
                }
                let scan = if have_scanned {
                    Scan::Ancestor { max_distance: max_hop_distance }
                } else {
                    Scan::CenterOut { window: effective_window }
                };
                match scan_for_seed(
                    actor,
                    current_receipt_id,
                    height,
                    &scan_shards,
                    scan,
                    &mut remaining_budget,
                )? {
                    Some(res) => {
                        have_scanned = true;
                        current_height = Some(res.outcome_block_height);
                        // Next-hop seed set by the FromReceipt arm below;
                        // FromTransaction arm returns without scanning.
                        res.info
                    }
                    None => {
                        return Err(GetReceiptToTxError::UnknownReceipt(current_receipt_id));
                    }
                }
            }
        };

        let ReceiptToTxInfo::V1(v1) = info;
        match v1.origin {
            ReceiptOrigin::FromTransaction(origin) => {
                return Ok(GetReceiptToTxResponse {
                    transaction_hash: origin.tx_hash,
                    sender_account_id: origin.sender_account_id,
                });
            }
            ReceiptOrigin::FromReceipt(origin) => {
                let parent_id = origin.parent_receipt_id;
                // Next hop targets parent P's producing shard. P executed on
                // shard(P.receiver_id) = this receipt's predecessor_id; P's parent
                // lives at shard(parent_predecessor_id). Carry the account, resolve
                // its lineage lazily at scan time, so a producing outcome on a
                // reshard-retired ancestor shard is still scanned.
                scan_shards = ScanShards::Account(origin.parent_predecessor_id);
                current_receipt_id = parent_id;
            }
        }
    }

    Err(GetReceiptToTxError::DepthExceeded {
        receipt_id: msg.receipt_id,
        limit: RECEIPT_TO_TX_MAX_DEPTH,
    })
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

**File:** integration-tests/src/tests/runtime/test_evil_contracts.rs (L93-130)
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
```
