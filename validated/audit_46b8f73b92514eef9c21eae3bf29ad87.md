### Title
BAL parallel-execution path trusts attacker-supplied Block Access List values for mid-block reads instead of verified prior-transaction outputs, allowing a self-consistent but spec-incorrect block to pass reth's post-execution checks - (File: `crates/engine/tree/src/tree/payload_processor/bal/worker.rs`, `crates/engine/tree/src/tree/payload_processor/bal/execute.rs`)

### Summary
The BAL (EIP-7928 Block Access List) parallel execution path lets each transaction worker read storage/account values that other, earlier-in-block transactions are declared to have written, by consulting the **received, unverified BAL** rather than the actual output of executing those earlier transactions. The canonical executor then simply commits each worker's pre-computed `ResultAndState` in order, without re-deriving it from genuinely accumulated state. Only a debug-level divergence log compares the final rebuilt BAL to the received one; no rejection occurs mid-execution. This mirrors the reported class of bug: using potentially stale/attacker-controlled positional data to decide what value an execution step "sees," instead of anchoring to the true dependency, letting an attacker shift the underlying values without breaking the block's own internal self-consistency checks.

### Finding Description
In the BAL executor, each worker installs the **input** BAL directly into its own speculative database: [1](#0-0) 

Specifically: [1](#0-0) 

`worker_state` is built `with_bal(received_bal_revm)`, and `set_bal_index(...)` tells revm's BAL-aware database to serve reads for state a given transaction should see "as declared by the BAL up to this index," not as actually produced by real execution of the preceding transactions. The transaction is then executed with `execute_transaction_without_commit`, and the resulting `ResultAndState` is sent back untouched.

The canonical/main thread then commits these worker-computed results directly, in order, without recomputing them against the true accumulated canonical state: [2](#0-1) 

The only consistency check between the received BAL and what execution actually produced is a **debug-level log**, not an error, not a rejection: [3](#0-2) 

The module doc block explicitly states the scope limitation: "This path validates the rebuilt block-level BAL hash after post-execution. It does not yet run per-transaction fragment checks." [4](#0-3) 

Downstream, post-execution consensus validation for Amsterdam blocks only checks that the **final aggregate** BAL hash matches the header — it never checks that individual declared writes correspond to values that a genuine serial execution would have produced at that point: [5](#0-4) 

Because the state root, receipts root, gas-used, and BAL hash used in post-execution validation are all derived from the very execution that consumed the (possibly manipulated) BAL, a block producer who supplies a self-consistent but "wrong" BAL — i.e., one that declares plausible-looking but incorrect values for storage/account state visible to a later transaction — can produce an internally coherent output. reth (running the BAL path) will accept this block as valid because every one of its own checks compares the output against artifacts computed from the same tainted execution. A client that performs genuine serial execution (which does not consult BAL values at all, deriving everything from real sequential dependency) will compute different actual results, and thus a different state root / receipts root than what the malicious block header commits to.

This is functionally identical to the reported vulnerability's root cause: relying on positional/declared external data to resolve what a computation should read/see, rather than the ground truth dependency, letting an attacker manipulate outcomes while every locally-observable invariant (bundle sizes, root hashes derived from the same tainted path) remains self-consistent.

### Impact Explanation
This breaks the equality between reth's BAL fast-path execution and canonical serial execution mandated by the EIP-7928/Ethereum spec. A block that reth (validating via the BAL path) accepts and computes a valid header state/receipts root for may not match what other clients (or reth itself, running the serial fallback) would compute from real transaction execution. This is a **Critical/High** severity class of issue per the given rubric: a state root or receipts root differing from the spec-mandated full recompute, or non-deterministic execution between the parallel/BAL path and the serial path, potentially causing a consensus split between reth nodes using the BAL path and clients that do full serial execution, or between reth's own BAL-fast-path output and its serial-fallback output for the same block.

### Likelihood Explanation
Exploitation requires the ability to submit or build a block/payload carrying a BAL whose declared intra-block values are internally self-consistent with the block's own EVM outputs but do not correspond to true sequential execution — achievable by a malicious block builder/proposer since the BAL and the transactions are both attacker-controlled at construction time; reth's validator does not independently re-derive per-transaction reads. The code's own module documentation acknowledges the missing "per-transaction fragment checks" and `bal_path_eligible`'s TODO notes it needs "stronger gating before enabling on mainnet," corroborating that this is a known-incomplete safety boundary. [6](#0-5) 

### Recommendation
Do not let workers resolve mid-block reads purely from the *received* BAL without verification. Either (a) enforce true sequential dependency for any storage/account slot that a later transaction in the same block also touches (falling back to serial execution or per-fragment validation for those slots), or (b) after computing the canonical execution, explicitly verify per-transaction/per-slot that every BAL-declared value actually used as an input by a worker matches the value that would result from genuinely applying all prior committed transactions, rejecting the block (not just logging) on any mismatch before accepting the derived roots.

### Proof of Concept
1. Attacker builds a block with two transactions: Tx1 (real code) and Tx2, which reads storage slot `S` of contract `C` and executes different logic branches depending on its value.
2. Attacker crafts a BAL declaring that Tx1 writes an incorrect value `V'` to slot `S` (different from what Tx1's real bytecode execution would actually produce, `V`).
3. Worker executing Tx2 uses `with_bal(received_bal_revm)` and reads `S` as `V'` (per `BlockAccessIndex`), not `V`, causing Tx2 to take a different execution branch than true serial execution would.
4. Canonical executor commits Tx1's real result and Tx2's `V'`-branch result in order; the resulting bundle state, receipts, and rebuilt BAL are all self-consistent with this flawed execution.
5. Header commits to the state/receipts root and BAL hash produced by this execution; reth's `validate_block_post_execution_with_bal_hashes` passes because it only compares reth's own freshly computed hashes against the header (both derived from the same tainted BAL-driven run) — see `crates/ethereum/consensus/src/validation.rs` lines 108-127.
6. A spec-compliant client performing genuine serial execution computes `V` for slot `S` at the time Tx2 runs, takes the other branch, and derives a different state/receipts root — rejecting the block that reth's BAL path accepted.

### Citations

**File:** crates/engine/tree/src/tree/payload_processor/bal/worker.rs (L74-98)
```rust
            let database = make_db(true).map_err(BalWorkerError::Setup)?;
            let mut worker_state = State::builder()
                .with_database(database)
                .with_bal(received_bal_revm)
                .with_bundle_update()
                .build();
            let evm = evm_config.evm_with_env(&mut worker_state, evm_env);
            let mut executor = evm_config.create_executor_with_state(evm, ctx.clone());

            loop {
                let (index, tx) = crossbeam_channel::select_biased! {
                    recv(abort_rx) -> _ => break,
                    recv(tx_rx) -> msg => match msg {
                        Ok(ix_tx) => ix_tx,
                        Err(_) => break,
                    },
                };
                let tx = tx.map_err(|e| BalWorkerError::Transaction(Box::new(e)))?;
                let signer = *tx.signer();
                let tx_gas_limit = tx.tx().gas_limit();

                executor.evm_mut().db_mut().set_bal_index(BlockAccessIndex::new(index as u64 + 1));
                let result = executor
                    .execute_transaction_without_commit(tx)
                    .map_err(BalWorkerError::Execution)?;
```

**File:** crates/engine/tree/src/tree/payload_processor/bal/execute.rs (L146-172)
```rust
        let mut senders = Vec::with_capacity(transaction_count);
        let mut last_sent_len = 0usize;
        for output in ordered_worker_outputs(&result_rx, transaction_count) {
            let output = output?;

            gas_tracker.validate_tx_limit(output.tx_gas_limit)?;
            gas_tracker.record_result(output.result.result());
            canonical_executor.evm_mut().db_mut().bump_bal_index();

            let _ = canonical_executor.commit_transaction(output.result);
            senders.push(output.signer);

            let current_len = canonical_executor.receipts().len();
            if current_len > last_sent_len {
                last_sent_len = current_len;
                if let Some(receipt) = canonical_executor.receipts().last() {
                    let tx_index = current_len - 1;
                    let _ = receipt_tx.send(IndexedReceipt::new(tx_index, receipt.clone()));
                }
            }
        }
        drop(abort_guard);

        canonical_executor.evm_mut().db_mut().bump_bal_index();
        let block_result = canonical_executor.apply_post_execution_changes()?;
        (block_result, senders)
    };
```

**File:** crates/engine/tree/src/tree/payload_processor/bal/execute.rs (L202-226)
```rust
fn take_built_bal_and_log_divergence<DB>(
    canonical_state: &mut State<DB>,
    received_bal: &AlloyBal,
) -> BlockAccessList
where
    DB: Database,
{
    let built_bal = canonical_state.take_built_alloy_bal().expect("with_bal_builder set");
    if tracing::enabled!(target: "engine::tree::payload_processor::bal", tracing::Level::DEBUG) &&
        built_bal.as_slice() != received_bal.as_slice()
    {
        let rebuilt = compute_block_access_list_hash(built_bal.as_slice());
        let expected = compute_block_access_list_hash(received_bal.as_slice());
        let div = received_bal.diff(built_bal.as_slice());
        tracing::debug!(
            target: "engine::tree::payload_processor::bal",
            %rebuilt,
            %expected,
            %div,
            "first BAL divergence",
        );
    }

    built_bal
}
```

**File:** crates/engine/tree/src/tree/payload_processor/bal/mod.rs (L1-9)
```rust
//! BAL-driven parallel block execution.
//!
//! The engine uses this path when an Amsterdam block carries a decoded EIP-7928
//! Block-Level Access List (BAL). Workers execute transactions against the EVM's BAL state. The
//! main thread commits worker results to a canonical executor in transaction order.
//!
//! Consensus validation checks the BAL item-cost bound before this path runs. This path validates
//! the rebuilt block-level BAL hash after post-execution. It does not yet run per-transaction
//! fragment checks. It does not yet report rich undeclared-access diagnostics.
```

**File:** crates/ethereum/consensus/src/validation.rs (L108-127)
```rust
    // Validate that the header block access list hash matches the calculated block access list hash
    let is_allowed_pre_amsterdam_bal_hash = allow_bal_hashes &&
        !chain_spec.is_amsterdam_active_at_timestamp(block.header().timestamp()) &&
        block.header().block_access_list_hash().is_some();

    let is_amsterdam = chain_spec.is_amsterdam_active_at_timestamp(block.header().timestamp());
    if is_amsterdam && block_access_list_hash.is_none() {
        return Err(ConsensusError::BlockAccessListHashMissing)
    }

    if (is_amsterdam || is_allowed_pre_amsterdam_bal_hash) &&
        let Some(block_access_list_hash) = block_access_list_hash
    {
        let block_bal_hash = block.header().block_access_list_hash().unwrap_or_default();
        if block_access_list_hash != block_bal_hash {
            return Err(ConsensusError::BlockAccessListHashMismatch(
                GotExpected::new(block_access_list_hash, block_bal_hash).into(),
            ))
        }
    }
```

**File:** crates/engine/tree/src/tree/payload_validator.rs (L1108-1116)
```rust
    /// Returns true when the BAL execute path should be used for this block.
    // TODO: extend with stronger gating before enabling on mainnet:
    //   - Fork check: `Amsterdam.active_at_timestamp(env.evm_env.timestamp)`. Today a BAL only
    //     exists post-Amsterdam, so the BAL-presence check is a sufficient proxy. It is a proxy,
    //     not a guarantee.
    //   - Tx-count threshold (`bal_execute_path_min_tx_count`): below the parallelism break-even
    //     point, provider setup and worker scheduling overhead can exceed the gain. Tune
    //     empirically once workers are parallel; meaningless while the commit loop is sequential.
    fn bal_path_eligible(&self, bal: Option<&DecodedBal>) -> Result<bool, InsertBlockErrorKind> {
```
