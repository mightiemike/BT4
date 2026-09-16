### Title
Reachable panic in concurrent block-building `commit_tx` when the Bouncer returns a non-`BlockFull` error - ([File: crates/blockifier/src/concurrency/worker_logic.rs])

### Summary
In concurrency-mode block building, `WorkerExecutor::commit_tx` assumes the only error `Bouncer::try_update` can return is `TransactionExecutorError::BlockFull`. Any other variant (e.g. `TransactionExecutionError::TransactionTooLarge` or a `StateError`) is not gracefully handled and instead triggers an explicit `panic!`, crashing the worker thread that is building the block. This mirrors the reported nimiq-consensus bug class: a handler assumes a narrow invariant about a value derived from untrusted/external input (there, "locator hash is always a macro block"; here, "the bouncer's update error is always `BlockFull`"), and when that assumption is violated the code reaches an unhandled panic (CWE-617, Reachable Assertion) instead of returning a typed error.

### Finding Description
`Bouncer::try_update` computes a transaction's marginal resource weights via `get_tx_weights` and returns `Err(TransactionExecutorError::BlockFull)` only when the block is already too full to admit the tx once its capacity fits normally [1](#0-0) . However, `get_tx_weights`/the underlying weight computation can also surface `TransactionExecutorError::TransactionExecutionError(TransactionExecutionError::TransactionTooLarge)` when a single transaction's own weights already exceed `bouncer_config.block_max_capacity` — this is the exact same code path exercised by `verify_tx_weights_within_max_capacity`, which explicitly maps this outcome to `TransactionExecutionError::TransactionTooLarge` [2](#0-1)  and is tested to occur for a normal transaction whose sierra_gas/resources exceed the configured block capacity [3](#0-2) .

In the **sequential** path (`TransactionExecutor::execute`), any error from `try_update` — including `TransactionTooLarge` — is simply propagated to the caller as a typed `Result`, with no panic [4](#0-3) .

In the **concurrent** path (`WorkerExecutor::commit_tx`, used during real block building via `TransactionExecutor::new_with_pool`/`WorkerPool`), the code explicitly special-cases only `TransactionExecutorError::BlockFull`; every other error variant falls into a wildcard arm that calls `panic!("Bouncer update failed. {error:?}: {error}")`: [5](#0-4) 

Because `try_update` can legitimately return `TransactionTooLarge` for a single, otherwise valid transaction (one whose accumulated resource footprint — state diff size, events, message segment length, storage-visit count, etc. — exceeds the configured block capacity even in isolation), a transaction sender that crafts such a transaction and gets it selected into a concurrently-executed batch can drive this code into the `panic!` branch.

### Impact Explanation
The panic occurs inside a `WorkerExecutor` thread that is part of the batcher's block-production `WorkerPool`. A panic here halts/crashes the worker thread handling block building rather than returning a controlled `TransactionTooLarge`/rejection result as the sequential path does. Depending on how the pool/executor supervises worker threads, this can stall or crash block production for the affected batcher instance, i.e. the sequencer becomes unable to close/build the current block — a network-availability impact ("a network unable to confirm new transactions") triggered purely by a single unprivileged transaction's resource shape, not by a malicious operator or peer.

### Likelihood Explanation
Likelihood is proportional to how easy it is to craft a transaction whose isolated resource weights exceed `bouncer_config.block_max_capacity` (e.g., via large state diffs, many events, or storage visits) while still passing gateway/mempool admission (which validates resource *bounds* declared by the sender against per-tx-type limits, not necessarily against the block-level bouncer capacity for every dimension such as `state_diff_size`, `message_segment_length`, or visited-storage counts). This is plausible for any sender who understands the bouncer weight dimensions, since the analogous test `test_transaction_too_large_sierra_gas_based` confirms a normal transaction can trigger `TransactionTooLarge` [3](#0-2) ; the missing piece is confirming exactly which weight dimensions are unguarded at gateway admission time versus checked only post-execution in the bouncer, which requires further investigation.

### Recommendation
In `WorkerExecutor::commit_tx`, replace the wildcard `panic!` with the same graceful handling used by the sequential `TransactionExecutor::execute` path: propagate the underlying error (e.g., as `CommitResult::TransactionExecutionError`/reject the transaction) instead of aborting the worker thread, mirroring how `TransactionExecutorError::TransactionExecutionError(...)` is already a typed, expected error variant elsewhere in the executor.

### Proof of Concept
1. Submit a single transaction (invoke or declare) engineered to have resource usage — once actually executed — that exceeds one dimension of `bouncer_config.block_max_capacity` in isolation (e.g., emit a very large number of events, or touch many distinct storage keys/segments), similar to the harness in `test_transaction_too_large_sierra_gas_based` [3](#0-2) , while still satisfying the gateway's stateless/stateful checks (which bound signature/calldata size and declared resource bounds but not all bouncer-tracked dimensions).
2. When the batcher selects this transaction into a concurrently-executed chunk, `WorkerExecutor::commit_tx` calls `Bouncer::try_update`, which returns `Err(TransactionExecutorError::TransactionExecutionError(TransactionExecutionError::TransactionTooLarge {..}))`.
3. `commit_tx`'s match only special-cases `BlockFull`; the `TransactionTooLarge` variant falls to the wildcard arm and executes `panic!("Bouncer update failed. {error:?}: {error}")` [6](#0-5) , crashing the worker thread mid-block-build.

**Note on confidence**: I was unable to fully verify, within the available tool budget, which exact bouncer weight dimensions are left unchecked at gateway/mempool admission time versus only detected post-execution by `get_tx_weights`/`try_update` — this is the key fact needed to fully confirm end-to-end reachability from an unprivileged transaction. This should be validated further (reviewing `crates/blockifier/src/bouncer.rs::get_tx_weights` in full and the gateway's `validate_resource_bounds`/mempool admission checks) before treating this as fully proven rather than a strong structural analog.

### Citations

**File:** crates/blockifier/src/bouncer.rs (L624-690)
```rust
    /// Updates the bouncer with a new transaction.
    // TODO(Dan): refactor to reduce the number of arguments.
    #[allow(clippy::too_many_arguments)]
    pub fn try_update<S: StateReader>(
        &mut self,
        state_reader: &S,
        tx_state_changes_keys: &StateChangesKeys,
        tx_execution_summary: &ExecutionSummary,
        tx_builtin_counters: &CairoPrimitiveCounterMap,
        tx_resources: &TransactionResources,
        versioned_constants: &VersionedConstants,
        receipt_l2_gas: GasAmount,
    ) -> TransactionExecutorResult<()> {
        // The countings here should be linear in the transactional state changes and execution info
        // rather than the cumulative state attributes.
        let marginal_state_changes_keys =
            tx_state_changes_keys.difference(&self.state_changes_keys);
        let marginal_executed_class_hashes = tx_execution_summary
            .executed_class_hashes
            .difference(&self.get_executed_class_hashes())
            .cloned()
            .collect();
        let n_marginal_visited_storage_entries = tx_execution_summary
            .visited_storage_entries
            .difference(&self.visited_storage_entries)
            .count();
        let tx_weights = get_tx_weights(
            state_reader,
            &marginal_executed_class_hashes,
            n_marginal_visited_storage_entries,
            tx_resources,
            &marginal_state_changes_keys,
            versioned_constants,
            tx_builtin_counters,
            &self.bouncer_config,
            receipt_l2_gas,
        )?;

        let tx_bouncer_weights = tx_weights.bouncer_weights;

        // Check if the transaction can fit the current block available capacity.
        let err_msg = format!(
            "Addition overflow. Transaction weights: {tx_bouncer_weights:?}, block weights: {:?}.",
            self.get_bouncer_weights()
        );
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
        if !self.bouncer_config.has_room(next_accumulated_weights) {
            let exceeded_weights =
                self.bouncer_config.get_exceeded_weights(next_accumulated_weights);
            log::debug!(
                "Transaction cannot be added to the current block, block capacity reached; \
                 transaction weights: {:?}, block weights: {:?}. Block max capacity reached on \
                 fields: {}",
                tx_weights.bouncer_weights,
                self.get_bouncer_weights(),
                exceeded_weights
            );
            // Record the block-full metric only once per block. Later candidate txs that also do
            // not fit (subsequent chunks / executor invocations share this bouncer) would otherwise
            // inflate the counter into a per-rejected-tx count instead of a per-block count.
            if !self.block_full_recorded {
                record_exceeded_bouncer_resources(&exceeded_weights);
                self.block_full_recorded = true;
            }
            Err(TransactionExecutorError::BlockFull)?
        }
```

**File:** crates/blockifier/src/bouncer.rs (L1077-1104)
```rust
// TODO(Dan): refactor to reduce the number of arguments.
#[allow(clippy::too_many_arguments)]
pub fn verify_tx_weights_within_max_capacity<S: StateReader>(
    state_reader: &S,
    tx_execution_summary: &ExecutionSummary,
    tx_builtin_counters: &CairoPrimitiveCounterMap,
    tx_resources: &TransactionResources,
    tx_state_changes_keys: &StateChangesKeys,
    bouncer_config: &BouncerConfig,
    versioned_constants: &VersionedConstants,
    receipt_l2_gas: GasAmount,
) -> TransactionExecutionResult<()> {
    let tx_weights = get_tx_weights(
        state_reader,
        &tx_execution_summary.executed_class_hashes,
        tx_execution_summary.visited_storage_entries.len(),
        tx_resources,
        tx_state_changes_keys,
        versioned_constants,
        tx_builtin_counters,
        bouncer_config,
        receipt_l2_gas,
    )?
    .bouncer_weights;

    bouncer_config.within_max_capacity_or_err(tx_weights)
}

```

**File:** crates/blockifier/src/bouncer_test.rs (L485-526)
```rust
#[rstest]
fn test_transaction_too_large_sierra_gas_based(block_context: BlockContext) {
    let mut state = test_state(&block_context.chain_info, Fee(0), &[]);
    let mut transactional_state = TransactionalState::create_transactional(&mut state);
    let block_max_capacity = BouncerWeights { sierra_gas: GasAmount(20), ..Default::default() };
    let bouncer_config = BouncerConfig { block_max_capacity, ..Default::default() };

    // Use gas amount > block_max_capacity's.
    let exceeding_gas = GasAmount(30);
    let execution_summary = ExecutionSummary::default();
    let builtin_counters = CairoPrimitiveCounterMap::default();
    let tx_resources = TransactionResources {
        computation: ComputationResources { sierra_gas: exceeding_gas, ..Default::default() },
        ..Default::default()
    };
    let tx_state_changes_keys = transactional_state.to_state_diff().unwrap().state_maps.keys();

    let result = verify_tx_weights_within_max_capacity(
        &transactional_state,
        &execution_summary,
        &builtin_counters,
        &tx_resources,
        &tx_state_changes_keys,
        &bouncer_config,
        &block_context.versioned_constants,
        GasAmount::ZERO,
    )
    .map_err(TransactionExecutorError::TransactionExecutionError);

    let expected_weights = BouncerWeights {
        sierra_gas: exceeding_gas,
        n_txs: 1,
        proving_gas: exceeding_gas,
        ..BouncerWeights::empty()
    };

    assert_matches!(result, Err(
        TransactionExecutorError::TransactionExecutionError(
            TransactionExecutionError::TransactionTooLarge { max_capacity, tx_size }
        )
    )  if *max_capacity == bouncer_config.block_max_capacity && *tx_size == expected_weights);
}
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L148-182)
```rust
    pub fn execute(
        &mut self,
        tx: &Transaction,
    ) -> TransactionExecutorResult<TransactionExecutionOutput> {
        let mut transactional_state = TransactionalState::create_transactional(
            self.block_state.as_mut().expect(BLOCK_STATE_ACCESS_ERR),
        );

        // Executing a single transaction cannot be done in a concurrent mode.
        let concurrency_mode = false;
        let tx_execution_result =
            tx.execute_raw(&mut transactional_state, &self.block_context, concurrency_mode);
        match tx_execution_result {
            Ok(tx_execution_info) => {
                let state_diff = transactional_state.to_state_diff()?.state_maps;
                let tx_state_changes_keys = state_diff.keys();
                lock_bouncer(&self.bouncer).try_update(
                    &transactional_state,
                    &tx_state_changes_keys,
                    &tx_execution_info.summarize(&self.block_context.versioned_constants),
                    &tx_execution_info.summarize_builtins(),
                    &tx_execution_info.receipt.resources,
                    &self.block_context.versioned_constants,
                    tx_execution_info.receipt.gas.l2_gas,
                )?;
                transactional_state.commit();

                Ok((tx_execution_info, state_diff))
            }
            Err(error) => {
                transactional_state.abort();
                Err(TransactionExecutorError::TransactionExecutionError(error))
            }
        }
    }
```

**File:** crates/blockifier/src/concurrency/worker_logic.rs (L347-364)
```rust
            // Ask the bouncer if there is room for the transaction in the block.
            let bouncer_result = self.bouncer.lock().expect("Bouncer lock failed.").try_update(
                &tx_versioned_state,
                &tx_state_changes_keys,
                &execution_summary,
                &tx_execution_info.summarize_builtins(),
                &tx_execution_info.receipt.resources,
                &self.block_context.versioned_constants,
                tx_execution_info.receipt.gas.l2_gas,
            );
            if let Err(error) = bouncer_result {
                match error {
                    TransactionExecutorError::BlockFull => return Ok(CommitResult::NoRoomInBlock),
                    _ => {
                        // TODO(Avi, 01/07/2024): Consider propagating the error.
                        panic!("Bouncer update failed. {error:?}: {error}");
                    }
                }
```
