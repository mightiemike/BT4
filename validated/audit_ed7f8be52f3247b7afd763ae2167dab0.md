### Title
BAL-driven parallel execution path trusts an attacker-supplied Block Access List for state reads without verifying it against real committed state - ([File: crates/engine/tree/src/tree/payload_processor/bal/worker.rs])

### Summary
When an incoming Amsterdam payload carries a decoded EIP-7928 Block Access List (BAL), reth's engine tree executes the block through the BAL-parallel path (`bal_path_eligible` / `execute_block_bal`) instead of the standard serial path. In this path, worker threads execute transactions speculatively against a `State` built `with_bal(received_bal_revm)` — i.e., using the *submitted, attacker-controlled* BAL contents as a read source — rather than the real database. Their outputs are then committed directly onto a canonical state that is otherwise backed by the real provider database. The module's own documentation admits this path "does not yet run per-transaction fragment checks", meaning there is no verification that the BAL's declared account/storage values actually match the parent's committed state before they are used to drive execution.

### Finding Description
The BAL execution flow is:

1. `payload_validator.rs::bal_path_eligible` gates on Amsterdam being active and a BAL being present in the payload — it performs no correctness check on the BAL's contents: [1](#0-0) 

2. `execute_block_bal` decodes the BAL from the untrusted execution payload and hands it straight to `bal::execute_block`: [2](#0-1) 

3. Each worker builds its own EVM state using `with_bal(received_bal_revm)`, where `received_bal_revm` is decoded directly from the payload's BAL bytes — this is the exact data an adversarial block proposer controls: [3](#0-2) 

4. The canonical executor is built over the *real* provider-backed database (`make_db(false)`), but worker outputs — computed using the untrusted BAL as their read source — are committed onto it directly, without cross-checking the worker's assumed reads against the real DB values for the same slots: [4](#0-3) 

5. The module doc explicitly discloses the missing safeguard: the BAL path "validates the rebuilt block-level BAL hash after post-execution. It does not yet run per-transaction fragment checks": [5](#0-4) 

6. Post-execution validation only checks (a) gas used vs header, (b) receipts root, (c) requests hash, and (d) that the *rebuilt* BAL hash matches the *header's declared* BAL hash: [6](#0-5) 

Crucially, every one of these header fields (`state_root`, `receipts_root`, `gas_used`, `requests_hash`, `block_access_list_hash`) is self-declared by the block proposer who also crafted the BAL. If a malicious proposer engineers a BAL whose declared account/storage values diverge from the real parent post-state (e.g., claims a stale/incorrect balance or storage slot), workers will execute transactions against those false values, producing state changes that are internally self-consistent with the proposer's own (wrong) narrative but that diverge from what serial execution against the real, authoritative parent state would produce. Because the "rebuilt" BAL and resulting state root are derived from this same tainted execution, they will match the proposer's crafted header fields — passing all checks in `validate_block_post_execution_with_bal_hashes` — even though a node executing the same block via the standard serial path (which reads real state) would compute different receipts, gas usage, and a different state root, and correctly reject the block.

### Impact Explanation
This breaks the core equality that a cached/parallel/BAL-driven execution result must equal serial execution against the parent's actual committed state. A reth node that takes the BAL fast path can be tricked into accepting a block that the same node's serial path — or any spec-compliant client without a BAL fast path — would reject as invalid (wrong state root / receipts / gas), or conversely produce a divergent post-state that silently differs from consensus. This falls squarely into "non-deterministic execution between cached/prewarmed/JIT/parallel and serial paths" and "a valid chain marked invalid" / state root divergence from spec, both explicitly in-scope High/Critical impact categories.

### Likelihood Explanation
The BAL path is only reachable when Amsterdam is active and the payload declares a BAL — a normal condition for post-Amsterdam blocks, not a rare edge case. The gating logic (`bal_path_eligible`) does not require any external validator confirmation of BAL correctness before dispatch, and the code's own comments acknowledge the missing "per-transaction fragment checks" as a known gap rather than a hypothetical. This significantly raises confidence that this is a genuine, currently-unmitigated soundness gap in the BAL execution path rather than a purely theoretical scenario — though I was not able to trace the exact low-level read semantics of `revm`'s `State::with_bal`/`db_mut().set_bal_index` (which lives in the excluded `revm` crate) to conclusively confirm that BAL-declared values fully override real DB reads for every account/storage access versus being used only as a prefetch/parallelism hint with a fallback verification elsewhere in `revm` itself. This is the key remaining uncertainty.

### Recommendation
Before or during the BAL-parallel execution path, verify each BAL fragment (account/storage entries) against the real parent-state values it claims, or at minimum re-validate a sample/all of the executed transactions' pre-state reads against the authoritative database before committing worker outputs to canonical state. Implement the "per-transaction fragment checks" noted as a TODO in `crates/engine/tree/src/tree/payload_processor/bal/mod.rs`, and ensure `validate_block_post_execution` (or an earlier gate) fails closed whenever any BAL-declared read value cannot be confirmed against the real parent state, rather than only checking hash self-consistency of the rebuilt BAL.

### Proof of Concept
Conceptual PoC (cannot be fully executed without deeper `revm` internals, per uncertainty noted above):
1. A malicious block proposer builds a block where a transaction's behavior depends on a storage/balance read (e.g., a conditional branch or arithmetic result).
2. The proposer crafts an EIP-7928 BAL declaring a *false* value for that storage slot (different from the true parent-state value), computes execution outputs consistent with that false value, and sets `state_root`, `receipts_root`, `gas_used`, and `block_access_list_hash` in the header to match this self-consistent (but incorrect) result.
3. The proposer submits this payload via `engine_newPayload`. Because Amsterdam is active and a BAL is present, `bal_path_eligible` routes execution through `execute_block_bal` → `bal::execute_block`, whose workers execute using the attacker's false BAL values (`worker.rs:75-79`).
4. `validate_block_post_execution_with_bal_hashes` only compares the rebuilt/derived values against the header's self-declared values (`crates/ethereum/consensus/src/validation.rs:56-127`), all of which the attacker controls and made internally consistent — the block is accepted.
5. A node executing the same block via the serial path (real state reads) would compute a different result and reject it, demonstrating the divergence between the BAL/parallel path and serial execution.

### Citations

**File:** crates/engine/tree/src/tree/payload_validator.rs (L1116-1127)
```rust
    fn bal_path_eligible(&self, bal: Option<&DecodedBal>) -> Result<bool, InsertBlockErrorKind> {
        let has_bal = bal.is_some();
        let parallel_execution = has_bal && !self.config.disable_bal_parallel_execution();
        if parallel_execution && self.config.disable_bal_parallel_state_root() {
            return Err(InsertBlockErrorKind::Other(
                "disabling parallel state root is impossible when parallel execution is enabled"
                    .into(),
            ));
        }

        Ok(parallel_execution)
    }
```

**File:** crates/engine/tree/src/tree/payload_validator.rs (L1166-1188)
```rust
        let input_bal = env.decoded_bal.ok_or_else(|| {
            InsertBlockErrorKind::Other("BAL execute path: no decoded BAL available".into())
        })?;

        let make_db = |fill_on_miss| {
            let provider = make_state_provider(fill_on_miss)
                .map_err(crate::tree::payload_processor::bal::BalExecutionError::Provider)?;
            Ok(StateProviderDatabase::new(provider))
        };
        let execution_start = Instant::now();
        let ctx =
            self.execution_ctx_for(input).map_err(|e| InsertBlockErrorKind::Other(Box::new(e)))?;
        let (output, senders, built_bal) = crate::tree::payload_processor::bal::execute_block(
            &self.runtime,
            &self.evm_config,
            &make_db,
            input_bal,
            env.evm_env,
            ctx,
            env.transaction_count,
            handle.clone_transaction_receiver(),
            receipt_tx,
        )?;
```

**File:** crates/engine/tree/src/tree/payload_processor/bal/worker.rs (L70-98)
```rust
    scope.spawn(move |_| {
        let worker_result = (|| -> Result<(), BalWorkerError> {
            // Create a database with fill_on_miss=true ensuring misses
            // are inserted for the other workers.
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

**File:** crates/engine/tree/src/tree/payload_processor/bal/execute.rs (L115-176)
```rust
    let mut canonical_state = State::builder()
        .with_database(make_db(false)?)
        .with_bundle_update()
        .with_bal_builder()
        .build();

    let (block_result, senders) = {
        let (result_tx, result_rx) = crossbeam_channel::unbounded();
        let (abort_guard, abort_rx) = AbortGuard::new();

        for _ in 0..worker_count {
            worker::spawn_worker(
                scope,
                txs.clone(),
                abort_rx.clone(),
                result_tx.clone(),
                evm_config,
                make_db,
                Arc::clone(&input_bal_revm),
                evm_env.clone(),
                ctx.clone(),
            );
        }
        drop(result_tx);

        let mut gas_tracker =
            BlockGasTracker::new(block_gas_limit, enable_amsterdam_eip8037, tx_gas_limit_cap);
        let evm = evm_config.evm_with_env(&mut canonical_state, evm_env);
        let mut canonical_executor = evm_config.create_executor_with_state(evm, ctx.clone());

        canonical_executor.apply_pre_execution_changes()?;
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

    let built_bal = take_built_bal_and_log_divergence(&mut canonical_state, bal);

    canonical_state.merge_transitions(BundleRetention::Reverts);
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

**File:** crates/ethereum/consensus/src/validation.rs (L56-127)
```rust
    // Check if gas used matches the value set in header.
    if block.header().gas_used() != result.gas_used {
        return Err(ConsensusError::BlockGasUsed {
            gas: GotExpected { got: result.gas_used, expected: block.header().gas_used() },
            gas_spent_by_tx: gas_spent_by_transactions(&result.receipts),
        })
    }

    // Before Byzantium, receipts contained state root that would mean that expensive
    // operation as hashing that is required for state root got calculated in every
    // transaction This was replaced with is_success flag.
    // See more about EIP here: https://eips.ethereum.org/EIPS/eip-658
    if chain_spec.is_byzantium_active_at_block(block.header().number()) {
        let res = if let Some((receipts_root, logs_bloom)) = receipt_root_bloom {
            compare_receipts_root_and_logs_bloom(
                receipts_root,
                logs_bloom,
                block.header().receipts_root(),
                block.header().logs_bloom(),
            )
        } else {
            verify_receipts(
                block.header().receipts_root(),
                block.header().logs_bloom(),
                &result.receipts,
            )
        };

        if let Err(error) = res {
            let receipts = result
                .receipts
                .iter()
                .map(|r| Bytes::from(r.with_bloom_ref().encoded_2718()))
                .collect::<Vec<_>>();
            tracing::debug!(%error, ?receipts, "receipts verification failed");
            return Err(error)
        }
    }

    // Validate that the header requests hash matches the calculated requests hash
    if chain_spec.is_prague_active_at_timestamp(block.header().timestamp()) {
        let Some(header_requests_hash) = block.header().requests_hash() else {
            return Err(ConsensusError::RequestsHashMissing)
        };
        let requests_hash = result.requests.requests_hash();
        if requests_hash != header_requests_hash {
            return Err(ConsensusError::BodyRequestsHashDiff(
                GotExpected::new(requests_hash, header_requests_hash).into(),
            ))
        }
    }

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
