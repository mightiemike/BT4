### Title
Unbounded native recursion in `Chain::get_recursive_transaction_results` allows an RPC caller to crash a node via stack overflow - ([File: chain/chain/src/chain.rs])

### Summary
`Chain::get_recursive_transaction_results` performs an uncontrolled recursive walk of a transaction's execution-outcome/receipt tree with no depth limit, directly analogous to the CVE-2021-30471 pattern of unbounded recursion (`PdfNamesTree::AddToDictionary`) leading to a stack overflow. The receipt tree depth is attacker-controlled: any account can submit a transaction whose function-call chains produce a long linear chain of receipts (each receipt spawning exactly one child receipt via a self-call, as demonstrated by the `max_self_recursion_delay`/`recurse` test contracts), and later trigger a lookup of the final transaction result for that hash, causing the node process performing the lookup to recurse once per receipt in the chain.

### Finding Description
`get_recursive_transaction_results` is implemented as true (non-tail) native recursion: [1](#0-0) 

It is invoked by `Chain::get_final_transaction_result`, the routine responsible for producing the `FinalExecutionOutcomeView` for a given transaction hash by walking every receipt spawned (directly or transitively) by that transaction: [2](#0-1) 

Unlike other recursive tree-walking code in this codebase that has since been hardened against exactly this bug class — e.g. `get_subtree_size` in `trie_recording.rs`, which explicitly uses an iterative queue "to avoid any potential stack overflows" — and `handle_receipt_to_tx` in `view_client_actor.rs`, which enforces a hard `RECEIPT_TO_TX_MAX_DEPTH = 1000` bound via an iterative loop, `get_recursive_transaction_results` has no such protection: [3](#0-2) [4](#0-3) 

The depth of the receipt chain that a single submitted transaction can generate is bounded only by gas, not by any protocol-level structural limit, as demonstrated by the test contract's self-recursive cross-contract call chain, which can reach a depth in the hundreds with a single `MAX_GAS` function call: [5](#0-4) [6](#0-5) 

Because each hop of the receipt chain (`SuccessReceiptId` → child receipt → child's own further receipts, via `outcome.receipt_ids`) adds one native stack frame in `get_recursive_transaction_results`, a sufficiently deep chain (achievable by chaining many cheap self-calls or cross-shard calls across multiple transactions/blocks with modest gas per hop) can exceed the thread stack size when a node subsequently resolves the final transaction result for the originating transaction hash.

### Impact Explanation
An attacker-controlled account can submit an ordinary function-call transaction (no special privileges required) that, through self/cross-contract calls, produces a deep, linear receipt chain. When any node later resolves the transaction's final execution outcome via `get_final_transaction_result` (the standard flow for answering "what happened to my transaction"), the unbounded recursion can overflow the native stack of the thread performing the lookup, crashing that process. This is a transaction-triggered denial-of-service against the node servicing the lookup — the same bug class (uncontrolled recursion → stack overflow) as CVE-2021-30471, but reachable here from an ordinary submitted transaction rather than a hostile PDF file.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to engineer a transaction whose receipt DAG has a long linear depth (not merely wide fan-out), which consumes gas per hop and is thus rate-limited by the transaction's gas budget, but the test suite itself shows depths in the hundreds are trivially reachable with a single `MAX_GAS` transaction, and multi-transaction/multi-block chaining could extend this arbitrarily. No special permissions, staking, or validator status are needed — any transaction signer can construct the input.

### Recommendation
Convert `get_recursive_transaction_results` to an iterative, explicit-stack (or worklist/queue) traversal, mirroring the pattern already used in `get_subtree_size` (comment: "Non recursive approach to avoid any potential stack overflows") and the depth-bounded loop used in `handle_receipt_to_tx`. Additionally, impose an explicit maximum traversal depth/count (returning a clear error such as `DepthExceeded` on breach) so that pathological receipt trees cannot exhaust resources even under an iterative implementation.

### Proof of Concept
1. Deploy/use the test contract exposing `max_self_recursion_delay` or `recurse` (already present in `near-test-contracts`), which each self-call spawns exactly one further action receipt to the same account.
2. Submit a function call with `MAX_GAS` invoking this recursive self-call pattern; per the existing test `slow_test_self_delay`, this alone produces a receipt chain of depth in the range 56–221 for a single transaction, and can be extended arbitrarily by chaining further transactions once the recursion budget of one transaction is exhausted.
3. Query the transaction's final execution result (`get_final_transaction_result`) on a node; the resulting call into `get_recursive_transaction_results` recurses once per receipt in the chain, consuming stack proportional to chain depth.
4. Repeating/extending the chain length (e.g. via multiple chained transactions or larger gas budgets) increases recursion depth until the thread's stack is exhausted, crashing the node process handling the lookup. [1](#0-0)

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

**File:** chain/client/src/view_client_actor.rs (L1318-1381)
```rust
const RECEIPT_TO_TX_MAX_DEPTH: u32 = 1000;

fn handle_receipt_to_tx(
    actor: &ViewClientActor,
    msg: GetReceiptToTx,
) -> Result<GetReceiptToTxResponse, GetReceiptToTxError> {
    let hint_provided = msg.block_height.is_some();
    if msg.shard_id.is_some() && !hint_provided {
        return Err(GetReceiptToTxError::MalformedHint(
            "shard_id requires block_height".to_string(),
        ));
    }
    // `window` meaningless without hint. Reject explicitly so caller
    // supplying `{receipt_id, window: 999}` doesn't get silent accept
    // with param discarded.
    if msg.window.is_some() && !hint_provided {
        return Err(GetReceiptToTxError::MalformedHint("window requires block_height".to_string()));
    }
    let effective_window = msg.window.unwrap_or(DEFAULT_HINT_WINDOW);
    let max_hint_window = actor.config.receipt_to_tx_max_hint_window;
    let max_hop_distance = actor.config.receipt_to_tx_max_hop_distance;
    if hint_provided && effective_window > max_hint_window {
        return Err(GetReceiptToTxError::WindowTooLarge {
            requested: effective_window,
            maximum: max_hint_window,
        });
    }

    // tracks_all_shards required both modes: cross-shard historical
    // lookups need every shard's chain data locally.
    if !actor.config.tracked_shards_config.tracks_all_shards() {
        return Err(GetReceiptToTxError::Unsupported("node does not track all shards".to_string()));
    }
    // Column-only mode requires save_receipt_to_tx. Hint mode doesn't —
    // rebuilds origin from OutcomeIds + Receipts/Transactions.
    if !hint_provided && !actor.config.save_receipt_to_tx {
        return Err(GetReceiptToTxError::Unsupported(
            "receipt-to-tx mapping is disabled (save_receipt_to_tx=false) and no hint supplied"
                .to_string(),
        ));
    }

    // TODO(receipt-to-tx-bench): benchmark cold-RocksDB worst-case to tune
    // `receipt_to_tx_max_outcomes_per_request` default; current 20k is a
    // conservative estimate.
    // TODO(sharded-rpc): if a sharded variant lands, move coordination logic
    // out of view_client_actor into the sharded handler.
    let max_outcomes_per_request = actor.config.receipt_to_tx_max_outcomes_per_request;

    let mut current_receipt_id = msg.receipt_id;
    let mut current_height = msg.block_height;
    // Shards a column-miss scan inspects, resolved lazily at scan time (column
    // hits skip it). Caller `shard_id` seeds first scan; each `FromReceipt` hop
    // reseeds to parent's predecessor account.
    let mut scan_shards = msg.shard_id.map(ScanShards::Hint).unwrap_or(ScanShards::Enumerate);
    let mut remaining_budget = max_outcomes_per_request;
    // Monotonic. False → true on first scan-resolve, never reset. After
    // scan, `current_height` = parent's exact execution height; causality
    // bounds later ancestors at or before anchor → column-miss scans stay
    // `Ancestor + max_hop_distance`. Pre-first-scan anchor = caller's
    // literal hint, `CenterOut` spans both sides.
    let mut have_scanned = false;

    for _ in 0..RECEIPT_TO_TX_MAX_DEPTH {
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
