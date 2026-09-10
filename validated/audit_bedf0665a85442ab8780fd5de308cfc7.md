### Title
BAL-path block-gas admission check is a hand-rolled duplicate of the serial executor's check, risking divergent block-validity verdicts between the parallel BAL path and serial execution - (File: crates/engine/tree/src/tree/payload_processor/bal/execute.rs)

### Summary
The reported bug class is a helper that re-implements/skips the canonical validation logic for a special execution path instead of calling the single source of truth, so the special path can reach a different accept/reject verdict than the canonical path for the same input. In reth's Amsterdam BAL (EIP-7928 Block-Level Access List) parallel execution path, the ordered commit loop never calls the canonical `execute_transaction_without_commit` gas-admission check that the serial `EthBlockExecutor` uses. Instead it re-derives the same verdict with a separate, hand-written `BlockGasTracker::validate_tx_limit` function.

### Finding Description
Serial block execution (`BasicBlockExecutor::execute_one`, [1](#0-0) ) executes every transaction through `EthBlockExecutor::execute_transaction_without_commit`, which is the canonical place where the pre-tx admission check "does this tx's gas limit fit in the remaining block gas budget" is performed before a transaction is allowed to execute.

The BAL parallel-execution path (used automatically when an Amsterdam block carries a decoded EIP-7928 BAL, see [2](#0-1) ) does not call that entry point at all. Workers execute transactions speculatively and the canonical commit loop commits their results directly via `commit_transaction`: [3](#0-2) 

Because the real admission check is bypassed on this path, the module's own doc-comment states the check "must be replayed here for BAL and serial execution to reach the same block validity verdict," and reimplements it from scratch in `BlockGasTracker::validate_tx_limit`: [4](#0-3) 

This is structurally the same class of defect as the external report: a single canonical check (`ALLOWED_DIFFERENCE`/TWAP verification in the original report; block-gas admission in `execute_transaction_without_commit` here) is bypassed for one code path (WETH in the report; the BAL/parallel execution path here) and replaced by a separately maintained, hand-duplicated approximation (`latestResolver` special-casing WETH in the report; `BlockGasTracker::validate_tx_limit` here). Any future change to the canonical admission rule (e.g. a new EIP altering gas accounting, an additional per-tx precondition, or a change to how `tx_gas_limit_cap`/Amsterdam regular-vs-state gas lanes are computed) that is applied to `execute_transaction_without_commit` but not mirrored into `BlockGasTracker::validate_tx_limit` (or vice versa) makes the two paths reach different verdicts for the identical block: one path admits a transaction/block the other would reject.

The engine selects between the two paths based solely on whether the incoming block carries a decoded BAL (`parallel_bal_execution`, see [5](#0-4)  and [6](#0-5) ), meaning a producer can choose which verdict function reth evaluates against merely by including or omitting a BAL sidecar on structurally similar blocks.

### Impact Explanation
If the duplicated check ever diverges from the canonical one (present or future divergence), the BAL execution path and the serial execution path can render different accept/invalid verdicts on byte-identical blocks within the same reth node. This falls squarely into the "non-deterministic execution between cached/prewarmed/JIT/parallel and serial paths" High-impact category: a block that the canonical serial executor would reject as invalid (e.g., exceeding available block gas) could instead be accepted via the BAL path, or a valid block could be wrongly rejected only when it happens to route through the parallel/BAL commit loop.

### Likelihood Explanation
The duplication is real and permanent in the code (not a bug in a single revision) — the BAL path structurally cannot invoke `execute_transaction_without_commit` because workers already ran transactions out-of-band before the ordered commit loop runs, so the reimplementation in `BlockGasTracker` is required by design. The risk materializes whenever the two independently-maintained gas-accounting implementations are not kept in perfect lockstep, which is inherently harder to guarantee for two separate functions than for one shared function, especially across future EIP-driven gas-accounting changes (Amsterdam/EIP-8037 already added a second gas lane specifically requiring this duplicate to be updated in parallel with the canonical executor).

### Recommendation
Refactor the BAL commit loop to call the same underlying admission-check function used by `EthBlockExecutor::execute_transaction_without_commit` (e.g., extract it into a shared, single-source-of-truth helper invoked by both the serial executor and `BlockGasTracker`), rather than maintaining a parallel hand-written re-implementation of the gas-budget arithmetic. Add a property/differential test that asserts, for a broad matrix of gas-limit/Amsterdam configurations, that `BlockGasTracker::validate_tx_limit`'s verdict is byte-for-byte derived from (or delegates to) the canonical check rather than merely "documented as mirroring" it.

### Proof of Concept
No runnable PoC is provided because no currently-present numeric divergence between `BlockGasTracker::validate_tx_limit` and the canonical `execute_transaction_without_commit` gas check was proven in this pass (the vendored canonical check lives outside the in-scope crates and could not be diffed line-by-line). The finding documents the structural equality-breaking risk: shared logic duplicated across the serial and BAL/parallel execution paths instead of being expressed once, exactly the pattern the external report exploited (special-cased/duplicated validation bypassing the canonical check).

### Citations

**File:** crates/evm/evm/src/execute.rs (L591-620)
```rust
    fn execute_one(
        &mut self,
        block: &RecoveredBlock<<Self::Primitives as NodePrimitives>::Block>,
    ) -> Result<BlockExecutionResult<<Self::Primitives as NodePrimitives>::Receipt>, Self::Error>
    {
        let mut executor = self
            .strategy_factory
            .executor_for_block(&mut self.db, block)
            .map_err(BlockExecutionError::other)?;

        let has_bal = block.header().block_access_list_hash().is_some();

        if has_bal {
            executor.evm_mut().db_mut().bal_state.bal_builder = Some(Bal::new());
        } else {
            executor.evm_mut().db_mut().bal_state.bal_builder = None;
        }

        executor.apply_pre_execution_changes()?;

        if has_bal {
            executor.evm_mut().db_mut().bump_bal_index();
        }

        for tx in block.transactions_recovered() {
            executor.execute_transaction(tx)?;
            if has_bal {
                executor.evm_mut().db_mut().bump_bal_index();
            }
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

**File:** crates/engine/tree/src/tree/payload_processor/bal/execute.rs (L148-156)
```rust
        for output in ordered_worker_outputs(&result_rx, transaction_count) {
            let output = output?;

            gas_tracker.validate_tx_limit(output.tx_gas_limit)?;
            gas_tracker.record_result(output.result.result());
            canonical_executor.evm_mut().db_mut().bump_bal_index();

            let _ = canonical_executor.commit_transaction(output.result);
            senders.push(output.signer);
```

**File:** crates/engine/tree/src/tree/payload_processor/bal/execute.rs (L267-314)
```rust
    /// Verifies that the transaction's gas limit fits the block's remaining gas budget(s): the
    /// admission check `EthBlockExecutor::execute_transaction_without_commit` performs before
    /// executing a transaction.
    ///
    /// The commit loop never calls that entry point — workers execute speculatively and their
    /// results are committed directly via `commit_transaction` — so the check must be replayed
    /// here for BAL and serial execution to reach the same block validity verdict.
    ///
    /// Pre-Amsterdam there is one budget: the tx gas limit, capped by `tx_gas_limit_cap`
    /// (EIP-7825), must fit `block_gas_limit - cumulative_tx_gas_used`.
    ///
    /// Amsterdam (EIP-8037) splits gas into two lanes, each budgeted at `block_gas_limit`:
    /// - regular: the capped tx gas limit must fit the remaining regular budget
    /// - state: the full, uncapped tx gas limit must fit the remaining state budget, since state
    ///   gas is drawn from the reservoir above `tx_gas_limit_cap` (execution-specs
    ///   `check_block_gas_capacity`)
    fn validate_tx_limit(&self, tx_gas_limit: u64) -> Result<(), BlockExecutionError> {
        let block_gas_used = if self.enable_amsterdam_eip8037 {
            self.block_regular_gas_used
        } else {
            self.cumulative_tx_gas_used
        };
        let block_available_gas = self.block_gas_limit.saturating_sub(block_gas_used);
        let tx_min_gas_limit =
            self.tx_gas_limit_cap.map_or(tx_gas_limit, |cap| tx_gas_limit.min(cap));

        if tx_min_gas_limit > block_available_gas {
            return Err(BlockValidationError::TransactionGasLimitMoreThanAvailableBlockGas {
                transaction_gas_limit: tx_gas_limit,
                block_available_gas,
            }
            .into());
        }

        if self.enable_amsterdam_eip8037 {
            let state_gas_available =
                self.block_gas_limit.saturating_sub(self.block_state_gas_used);
            if tx_gas_limit > state_gas_available {
                return Err(BlockValidationError::TransactionGasLimitMoreThanAvailableBlockGas {
                    transaction_gas_limit: tx_gas_limit,
                    block_available_gas: state_gas_available,
                }
                .into());
            }
        }

        Ok(())
    }
```

**File:** crates/engine/tree/src/tree/payload_validator.rs (L719-737)
```rust
        // Execute the block and handle any execution errors.
        // The receipt root task is spawned before execution and receives receipts incrementally
        // as transactions complete, allowing parallel computation during execution.
        let execute_block_start = Instant::now();
        let execution_result = if parallel_bal_execution {
            self.execute_block_bal(env, &input, &handle, &make_state_provider)
        } else {
            let state_provider = make_state_provider(false);
            match state_provider {
                Ok(state_provider) => self.execute_block(
                    state_provider,
                    env,
                    &input,
                    &mut handle,
                    execution_state_hook,
                ),
                Err(err) => Err(err.into()),
            }
        };
```

**File:** crates/engine/tree/src/tree/payload_validator.rs (L1136-1188)
```rust
    /// 4. Calls [`crate::tree::payload_processor::bal::execute_block`].
    /// 5. Returns the rebuilt BAL for post-execution consensus validation.
    #[instrument(level = "debug", target = "engine::tree::payload_validator", skip_all)]
    #[expect(clippy::type_complexity)]
    fn execute_block_bal<Tx, Err, MakeStateProvider, T>(
        &self,
        env: ExecutionEnv<Evm>,
        input: &BlockOrPayload<T>,
        handle: &PayloadHandle<Tx, Err, N::Receipt>,
        make_state_provider: &MakeStateProvider,
    ) -> Result<
        (
            BlockExecutionOutput<N::Receipt>,
            Vec<Address>,
            ReceiptRootReceiver,
            Option<BlockAccessList>,
        ),
        InsertBlockErrorKind,
    >
    where
        Tx: ExecutableTxFor<Evm> + Send,
        Err: core::error::Error + Send + Sync + 'static,
        MakeStateProvider: Fn(bool) -> ProviderResult<StateProviderBox> + Sync,
        Evm: ConfigureEngineEvm<T::ExecutionData, Primitives = N>,
        T: PayloadTypes<BuiltPayload: BuiltPayload<Primitives = N>>,
        V: PayloadValidator<T, Block = N::Block>,
    {
        debug!(target: "engine::tree::payload_validator", "Executing block via BAL path");

        let (receipt_tx, result_rx) = self.spawn_receipt_root_task(env.transaction_count);
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
