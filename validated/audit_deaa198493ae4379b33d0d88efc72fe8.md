### Title
Program-load/ELF-verification cost of `ProgramCacheEntry::new_internal`'s reload path is not charged to the invoking transaction's compute-unit accounting, allowing CPU-cost/CU-charge mismatch via mass first-invocation of unique max-size BPF programs - ([File: program-runtime/src/program_cache_entry.rs], [File: cost-model/src/cost_model.rs])

### Summary
`ProgramCacheEntry::load` (invoked from `load_program_with_pubkey` during `TransactionBatchProcessor::replenish_program_cache`) runs `executable.verify::<RequisiteVerifier>()` whose real CPU cost scales with ELF/bytecode size, but this work happens outside the VM's metered execution and is never reflected in `executed_units`/`actual_execution_units` used by `CostModel::calculate_cost_for_executed_transaction` or `CostTracker::update_execution_cost`. Because `CostModel::get_estimated_execution_cost` derives `programs_execution_cost` purely from the attacker-controlled `ComputeBudgetInstruction::SetComputeUnitLimit` value, an attacker can declare an arbitrarily low CU limit for transactions that trigger a cold cache load/verify of a large, freshly-deployed program, causing the validator to spend CPU time proportional to program size while the cost tracker only debits the tiny declared CU amount from the block budget.

### Finding Description
When a program is deployed via `bpf_loader_upgradeable`, `deploy_program` (program-runtime/src/deploy.rs:79-117) verifies the ELF once using the stricter deployment environment, but stores only a `ProgramCacheEntry::new_unloaded(...)` (`ProgramCacheEntryType::Unloaded`) into the cache — the actually-loaded/verified `Executable` is discarded, not cached [1](#0-0) .

On the first invocation after the deploy visibility delay, `ProgramCache::extract` finds this `Unloaded` entry and explicitly refuses to reuse it (`break`), forcing a fresh cooperative-loading task [2](#0-1) . That task is serviced by `TransactionBatchProcessor::replenish_program_cache`, which calls `load_program_with_pubkey` [3](#0-2) , which in turn calls `ProgramCacheEntry::load`, re-parsing the ELF and running `executable.verify::<RequisiteVerifier>()` against the on-chain bytecode [4](#0-3) . This work is timed only via `LoadProgramMetrics`/`execute_timings` (a metrics-only path gated by `#[cfg(feature = "metrics")]`), not via any compute-unit counter [5](#0-4) .

Meanwhile, `CostModel::get_estimated_execution_cost` computes `programs_execution_cost` solely from `config.compute_unit_limit`, i.e. the value the attacker sets via `ComputeBudgetInstruction::SetComputeUnitLimit` [6](#0-5) . Post-execution truing up (`calculate_cost_for_executed_transaction`/`CostTracker::update_execution_cost`) is likewise driven only by `actual_execution_units`, i.e. VM-metered compute units consumed inside `execute_loaded_transaction`, which never includes the pre-execution ELF-verification time spent in `replenish_program_cache` [7](#0-6) . There is no separate charge or cap tied to per-program verification cost — the only mitigations are `MAX_LOADED_ENTRY_COUNT` (a cache size cap) and `hit_max_limit`/`limit_to_load_programs` (a per-batch cache-slot exhaustion guard), neither of which meters or bounds cumulative ELF-verification CPU time across many first-touched unique programs in a block [8](#0-7) [9](#0-8) .

### Impact Explanation
An attacker can construct a block-worth of transactions, each invoking a distinct, previously-deployed, near-max-size BPF program for the first time in that slot (or after eviction), each declaring a minimal `compute_unit_limit`. The leader must perform `RequisiteVerifier` verification proportional to bytecode size for every one of these unique programs before it can even begin metered execution, while the cost tracker only debits the small declared CU amount against `block_cost` limits. This allows an attacker to pack far more "real CPU work" into a block than the cost model accounts for, degrading leader liveness/throughput without breaching nominal block-cost limits — a resource-exhaustion / liveness-degradation impact.

### Likelihood Explanation
Preconditions are all within an unprivileged client's reach: fund and deploy P distinct BPF programs via the standard `bpf_loader_upgradeable` flow (write+deploy), then submit P invocation transactions with `SetComputeUnitLimit` set low. Feasibility is bounded mainly by the attacker's capital cost to deploy multiple large programs (rent-exempt reserve per max-size program account, plus per-chunk write fees) — these are real but recoverable/bounded costs and do not require any elevated access, matching "ordinary client" preconditions. The exploit is repeatable across slots as long as the attacker keeps producing freshly-cache-missed (unloaded/evicted) unique programs, e.g. via new deployments or waiting for eviction (`evict_using_random_selection`, `MAX_LOADED_ENTRY_COUNT = 1024`).

### Recommendation
Meter ELF loading/verification (and JIT compilation) time incurred during `ProgramCacheEntry::load`/`replenish_program_cache` and fold it into the transaction's compute-unit accounting (or a dedicated block-level "program-load" cost bucket) rather than solely relying on the caller-declared `compute_unit_limit`. Alternatively, charge a fixed or size-proportional cost model term for each program load into `CostModel::get_estimated_execution_cost`/`calculate_transaction_cost`, and enforce a per-block cap on total bytes of newly-loaded/verified program code, independent of `MAX_LOADED_ENTRY_COUNT`.

### Proof of Concept
Integration test plan (using `RuntimeTransaction`/`SimpleAddressLoader`/`TransactionBatchProcessor`, similar to `svm/tests/concurrent_tests.rs` harnesses):
1. Deploy P (e.g. 50) distinct BPF programs at (or near) the maximum permitted program size via the `bpf_loader_upgradeable` deploy flow, letting `deploy_program` insert `Unloaded` cache entries as in normal operation.
2. Advance past `DELAY_VISIBILITY_SLOT_OFFSET`, then in a single simulated block construct P transactions, each invoking a distinct program for the first time with `ComputeBudgetInstruction::set_compute_unit_limit` set to a minimal value (e.g. 300 CU).
3. Call `TransactionBatchProcessor::replenish_program_cache` (or the full `load_and_execute_sanitized_transactions` path) for the batch, measuring wall-clock time spent inside `load_program_with_pubkey`/`ProgramCacheEntry::load`/`verify::<RequisiteVerifier>()` via `LoadProgramMetrics.verify_code_us`.
4. In parallel, compute `CostModel::calculate_cost`/`calculate_cost_for_executed_transaction` for each transaction and feed them into `CostTracker::try_add`, recording `block_cost()`.
5. Assert that cumulative real verify time (`sum(verify_code_us)`) scales with total bytecode size of the P programs and is disproportionately large relative to `block_cost()` recorded by `CostTracker`, demonstrating the accounting gap.

### Citations

**File:** program-runtime/src/deploy.rs (L94-117)
```rust
    executable.verify::<RequisiteVerifier>().map_err(|err| {
        ic_logger_msg!(log_collector, "{}", err);
        InstructionError::InvalidAccountData
    })?;
    #[cfg(feature = "metrics")]
    {
        verify_code_time.stop();
        load_program_metrics.verify_code_us = verify_code_time.as_us();
    }
    // Insert but with program_runtime_environment
    let program_cache_entry = ProgramCacheEntry::new_unloaded(
        deployment_slot,
        ProgramCacheEntryOwner::try_from(loader_key)
            .map_err(|_| InstructionError::InvalidAccountData)?,
        program_runtime_environment,
    );
    if let Some(old_entry) = program_cache_for_tx_batch.find(program_id) {
        program_cache_entry.stats.merge_from(&old_entry.stats);
    }
    #[cfg(feature = "metrics")]
    {
        load_program_metrics.program_id = program_id.to_string();
    }
    program_cache_for_tx_batch.store_modified_entry(*program_id, Arc::new(program_cache_entry));
```

**File:** program-runtime/src/loaded_programs.rs (L116-117)
```rust
pub const MAX_LOADED_ENTRY_COUNT: usize = 1024;
pub const MAX_TOMBSTONE_AGE_IN_SLOTS: u64 = 2250; // 15 Minutes at 400ms slot time
```

**File:** program-runtime/src/loaded_programs.rs (L649-667)
```rust
                            if entry_in_same_branch {
                                let entry_is_effective =
                                    loaded_programs_for_tx_batch.slot >= entry.effective_slot();
                                let entry_to_return = if entry_is_effective {
                                    if !Self::matches_environment(
                                        entry,
                                        program_runtime_environment_for_execution,
                                    ) {
                                        // We found an entry that would work, had its environment
                                        // matched the one we're planning to use for this slot. A
                                        // sibling compiled against that environment may follow.
                                        continue;
                                    }
                                    if let ProgramCacheEntryType::Unloaded(_environment) =
                                        &entry.program
                                    {
                                        break;
                                    }
                                    entry.clone()
```

**File:** svm/src/transaction_processor.rs (L959-970)
```rust
            let program_to_store = program_to_load.map(|key| {
                // Load, verify and compile one program.
                let (program, last_modification_slot) = load_program_with_pubkey(
                    account_loader,
                    program_runtime_environment_for_execution,
                    &key,
                    self.slot,
                    execute_timings,
                )
                .expect("called load_program_with_pubkey() with nonexistent account");
                (key, program, last_modification_slot)
            });
```

**File:** svm/src/transaction_processor.rs (L972-990)
```rust
            if let Some((key, program, last_modification_slot)) = program_to_store {
                program_cache_for_tx_batch.loaded_missing = true;
                let mut global_program_cache = self.global_program_cache.write().unwrap();
                // Submit our last completed loading task.
                if global_program_cache.finish_cooperative_loading_task(
                    program_runtime_environment_for_execution,
                    self.slot,
                    key,
                    last_modification_slot,
                    program,
                ) && limit_to_load_programs
                {
                    // This branch is taken when there is an error in assigning a program to a
                    // cache slot. It is not possible to mock this error for SVM unit
                    // tests purposes.
                    *program_cache_for_tx_batch = ProgramCacheForTxBatch::new(self.slot);
                    program_cache_for_tx_batch.hit_max_limit = true;
                    return;
                }
```

**File:** program-runtime/src/program_cache_entry.rs (L207-220)
```rust
        let executable = Executable::load(elf_bytes, Arc::clone(&*program_runtime_environment))?;

        #[cfg(feature = "metrics")]
        {
            metrics.load_elf_us = load_elf_time.end_as_us();
        }

        #[cfg(feature = "metrics")]
        let verify_code_time = solana_svm_measure::measure::Measure::start("verify_code_time");
        executable.verify::<RequisiteVerifier>()?;
        #[cfg(feature = "metrics")]
        {
            metrics.verify_code_us = verify_code_time.end_as_us();
        }
```

**File:** program-runtime/src/program_metrics.rs (L247-271)
```rust
#[cfg(feature = "metrics")]
/// Time measurements for loading a single [ProgramCacheEntry].
#[derive(Debug, Default)]
pub struct LoadProgramMetrics {
    /// Program address, but as text
    pub program_id: String,
    /// Microseconds it took to `create_program_runtime_environment`
    pub register_syscalls_us: u64,
    /// Microseconds it took to `Executable::<InvokeContext>::load`
    pub load_elf_us: u64,
    /// Microseconds it took to `executable.verify::<RequisiteVerifier>`
    pub verify_code_us: u64,
    /// Microseconds it took to `executable.jit_compile`
    pub jit_compile_us: u64,
}

#[cfg(feature = "metrics")]
impl LoadProgramMetrics {
    pub fn submit_datapoint(&self, timings: &mut ExecuteDetailsTimings) {
        timings.create_executor_register_syscalls_us += self.register_syscalls_us;
        timings.create_executor_load_elf_us += self.load_elf_us;
        timings.create_executor_verify_code_us += self.verify_code_us;
        timings.create_executor_jit_compile_us += self.jit_compile_us;
    }
}
```

**File:** cost-model/src/cost_model.rs (L159-178)
```rust
    fn get_estimated_execution_cost(
        transaction: &impl TransactionMeta,
        feature_set: &FeatureSet,
    ) -> (u64, u64) {
        // if failed to process compute_budget instructions, the transaction will not be executed
        // by `bank`, therefore it should be considered as no execution cost by cost model.
        let (programs_execution_costs, loaded_accounts_data_size_cost) =
            match transaction.transaction_configuration(feature_set) {
                Ok(config) => (
                    u64::from(config.compute_unit_limit),
                    Self::calculate_loaded_accounts_data_size_cost(
                        config.loaded_accounts_data_size_limit,
                        feature_set,
                    ),
                ),
                Err(_) => (0, 0),
            };

        (programs_execution_costs, loaded_accounts_data_size_cost)
    }
```

**File:** cost-model/src/cost_tracker.rs (L273-299)
```rust
    pub fn update_execution_cost(
        &mut self,
        estimated_tx_cost: &TransactionCost<impl TransactionWithMeta>,
        actual_execution_units: u64,
        actual_loaded_accounts_data_size_cost: u64,
    ) {
        let actual_load_and_execution_units =
            actual_execution_units.saturating_add(actual_loaded_accounts_data_size_cost);
        let estimated_load_and_execution_units = estimated_tx_cost
            .programs_execution_cost()
            .saturating_add(estimated_tx_cost.loaded_accounts_data_size_cost());
        match actual_load_and_execution_units.cmp(&estimated_load_and_execution_units) {
            std::cmp::Ordering::Equal => (),
            std::cmp::Ordering::Greater => {
                self.add_transaction_execution_cost(
                    estimated_tx_cost,
                    actual_load_and_execution_units - estimated_load_and_execution_units,
                );
            }
            std::cmp::Ordering::Less => {
                self.sub_transaction_execution_cost(
                    estimated_tx_cost,
                    estimated_load_and_execution_units - actual_load_and_execution_units,
                );
            }
        }
    }
```
