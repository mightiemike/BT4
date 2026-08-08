### Title
CostModel::calculate_allocated_accounts_data_size ignores CPI/program-syscall based account reallocation, letting attackers grow AccountsDb storage past the block accounts-data-size budget for free - (File: cost-model/src/cost_model.rs)

### Summary
`CostModel::calculate_allocated_accounts_data_size` only inspects top-level instructions whose `program_id` equals the System Program and statically pattern-matches `CreateAccount`/`Allocate`/`AllocateWithSeed`/`CreateAccountWithSeed` space fields. Any account growth performed via CPI to the System Program, or via a BPF program's own `AccountInfo::resize`/realloc mechanism (the dominant real-world mechanism, used e.g. by SPL Token-2022 extensions), is invisible to this estimate and contributes `0` to `allocated_accounts_data_size`, even though it produces real, runtime-enforced account-data growth of up to `MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION` bytes per transaction.

### Finding Description
`CostModel::calculate_allocated_accounts_data_size` iterates only `transaction.program_instructions_iter()` (top-level compiled instructions) and calls `calculate_account_data_size_on_instruction`, which returns `SystemProgramAccountAllocation::None` for any `program_id != system_program::id()`: [1](#0-0) [2](#0-1) 

This function is used both for the pre-execution estimate (`calculate_cost`) and — critically — for the *actual* post-execution cost via `calculate_cost_for_executed_transaction`, which is what `Consumer::calculate_processed_transaction_costs` feeds into `CostTracker::try_add`/`add_transaction_cost`: [3](#0-2) 

Because `calculate_transaction_cost` re-derives `allocated_accounts_data_size` purely from the static top-level-instruction scan (not from any actually-measured execution result), a transaction that grows account data via:
- a CPI to the System Program's `Allocate`/`CreateAccount` (an *inner* instruction, not in `program_instructions_iter()`), or
- a BPF program calling `AccountInfo::resize()` (`sol_realloc` style growth, which never invokes the System Program at all)

is counted as `0` allocated bytes, regardless of how much data was actually committed to the account.

Meanwhile, the real runtime enforcement of account growth is a *separate* mechanism entirely disconnected from the cost model: `TransactionAccounts::update_accounts_resize_delta`/`can_data_be_resized` track a transaction-wide net `resize_delta` capped at `MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION` (`MAX_ACCOUNT_DATA_LEN * 2`, i.e. 20 MiB): [4](#0-3) [5](#0-4) 

This runtime check correctly bounds *net* per-transaction growth (it uses a running `resize_delta` counter updated on every resize, so repeated grow/shrink cannot bypass the 20 MiB cap), so it is not itself vulnerable to gross-churn undercounting. However, it is completely independent of, and never reported into, `CostTracker::allocated_accounts_data_size`/`MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA`: [6](#0-5) [7](#0-6) 

So the CostTracker's per-block account-data-growth budget (`MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` = 100 MB), whose purpose is to bound AccountsDb storage growth work per block proportionally to what is being scheduled/accounted, can be driven to `0` contribution by an attacker whose transactions grow account data exclusively through CPI or direct BPF-program `resize()`/realloc calls, while still committing real bytes to AccountsDb append-vec storage each up to the (unrelated, per-tx) 20 MiB `resize_delta` cap. No existing guard in `cost_tracker.rs`'s `would_fit`/`add_transaction_cost` compensates for this, since it trusts `tx_cost.allocated_accounts_data_size()` verbatim.

### Impact Explanation
An unprivileged user, using only an ordinary BPF program (many already-deployed programs, including standard token/associated-account program patterns, call `AccountInfo::resize`), can submit transactions that grow account data by real bytes committed to AccountsDb append-vecs while contributing `0` to `CostTracker::allocated_accounts_data_size`. Repeating this across many transactions in a block lets the attacker exceed the intended `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` (100 MB) growth-per-block budget arbitrarily (bounded only by block compute-unit/account-cost limits, not the accounts-data-size limit that is supposed to gate this specific resource). This is a disproportionate-storage-cost issue: the block-level throttle intended to keep AccountsDb append-vec growth work proportional to accounted fees/limits can be bypassed for CPI/syscall-driven reallocation, matching the "disproportionate storage and CPU cost" bounty category. It is a resource-accounting/DoS-adjacent gap in the cost model rather than a consensus-correctness or fund-safety bug, since the hard, correctness-critical per-transaction growth cap (`MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION`) is enforced independently and correctly in `transaction_accounts.rs`.

### Likelihood Explanation
Fully reachable by an unprivileged attacker with no special preconditions: any deployed BPF program that calls `AccountInfo::resize()` (a completely ordinary, widely-used operation, e.g. in `programs/sbf/rust/realloc/src/lib.rs`) or that CPIs to the System Program's `Allocate`/`CreateAccount` will trigger this undercount every single time such a transaction is processed, deterministically and repeatably, with no exotic timing or race conditions required.

### Recommendation
Extend the cost model's `allocated_accounts_data_size` accounting to be derived from the actual measured account-data growth during/after execution (e.g., surface and sum each account's `resize_delta`/pre-vs-post `data().len()` from `TransactionAccounts`/`LoadedTransaction` after execution) instead of statically re-scanning only top-level System Program instructions. At minimum, feed the authoritative post-execution net growth into `calculate_cost_for_executed_transaction` so `CostTracker`'s block-level accounts-data-size budget reflects real AccountsDb growth from CPI and program-syscall-driven reallocation, not just top-level System Program calls.

### Proof of Concept
Rust unit test (in `cost-model/src/cost_model.rs` test module) demonstrating the undercount:

```rust
#[test]
fn test_calculate_allocated_accounts_data_size_ignores_bpf_realloc() {
    // Build a transaction whose *only* writable-account growth happens via
    // a non-system-program instruction (simulating a BPF program that calls
    // AccountInfo::resize()/CPIs to the system program internally).
    let bpf_program_id = Pubkey::new_unique();
    let transaction = Transaction::new_unsigned(Message::new(
        &[Instruction::new_with_bytes(
            bpf_program_id,
            &[0u8], // opcode understood by e.g. programs/sbf/rust/realloc to grow account
            vec![AccountMeta::new(Pubkey::new_unique(), false)],
        )],
        Some(&Pubkey::new_unique()),
    ));
    let sanitized_tx = RuntimeTransaction::from_transaction_for_tests(transaction);

    // Cost model reports zero allocated bytes for this instruction...
    assert_eq!(
        CostModel::calculate_allocated_accounts_data_size(
            sanitized_tx.program_instructions_iter(),
            &FeatureSet::all_enabled()
        ),
        0
    );
    // ...even though executing this transaction via bank.load_execute_and_commit_transactions
    // (see programs/sbf/rust/realloc test harness) would grow the target account's data by
    // up to MAX_PERMITTED_DATA_INCREASE (10_240) bytes, which is a real byte-growth that is
    // never added to CostTracker::allocated_accounts_data_size / MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA.
}
```

Integration-level follow-up (in `svm/tests/integration_test.rs` alongside `simd83_account_reallocate`): compare `bank.write_cost_tracker().unwrap().report_stats(...)`'s `allocated_accounts_data_size` before/after committing N transactions that each call the `realloc` SBF test program to grow a fresh account by `MAX_PERMITTED_DATA_INCREASE` bytes, and assert `allocated_accounts_data_size` stays at `0` while `AccountSharedData::data().len()` on the target accounts sums to `N * MAX_PERMITTED_DATA_INCREASE`, demonstrating the accounted/real growth divergence.

### Citations

**File:** cost-model/src/cost_model.rs (L242-261)
```rust
    fn calculate_account_data_size_on_instruction(
        program_id: &Pubkey,
        instruction: SVMInstruction,
        feature_set: &FeatureSet,
    ) -> SystemProgramAccountAllocation {
        if program_id == &system_program::id() {
            if let Ok(instruction) =
                limited_deserialize(instruction.data, solana_packet::PACKET_DATA_SIZE as u64)
            {
                Self::calculate_account_data_size_on_deserialized_system_instruction(
                    instruction,
                    feature_set,
                )
            } else {
                SystemProgramAccountAllocation::Failed
            }
        } else {
            SystemProgramAccountAllocation::None
        }
    }
```

**File:** cost-model/src/cost_model.rs (L265-301)
```rust
    fn calculate_allocated_accounts_data_size<'a>(
        instructions: impl Iterator<Item = (&'a Pubkey, SVMInstruction<'a>)>,
        feature_set: &FeatureSet,
    ) -> u64 {
        let mut tx_attempted_allocation_size = Saturating(0u64);
        for (program_id, instruction) in instructions {
            match Self::calculate_account_data_size_on_instruction(
                program_id,
                instruction,
                feature_set,
            ) {
                SystemProgramAccountAllocation::Failed => {
                    // If any system program instructions can be statically
                    // determined to fail, no allocations will actually be
                    // persisted by the transaction. So return 0 here so that no
                    // account allocation budget is used for this failed
                    // transaction.
                    return 0;
                }
                SystemProgramAccountAllocation::None => continue,
                SystemProgramAccountAllocation::Some(ix_attempted_allocation_size) => {
                    tx_attempted_allocation_size += ix_attempted_allocation_size;
                }
            }
        }

        // The runtime prevents transactions from allocating too much account
        // data so clamp the attempted allocation size to the max amount.
        //
        // Note that if there are any custom bpf instructions in the transaction
        // it's tricky to know whether a newly allocated account will be freed
        // or not during an intermediate instruction in the transaction so we
        // shouldn't assume that a large sum of allocations will necessarily
        // lead to transaction failure.
        (MAX_PERMITTED_ACCOUNTS_DATA_ALLOCATIONS_PER_TRANSACTION as u64)
            .min(tx_attempted_allocation_size.0)
    }
```

**File:** core/src/banking_stage/consumer.rs (L490-520)
```rust
    fn calculate_processed_transaction_costs<'a, Tx: TransactionWithMeta>(
        bank: &Bank,
        transactions: &'a [Tx],
        processing_results: &[TransactionProcessingResult],
    ) -> Vec<Option<TransactionCost<'a, Tx>>> {
        let mut transaction_costs = Vec::with_capacity(processing_results.len());

        for (tx, processing_result) in transactions.iter().zip(processing_results) {
            let Some((executed_units, loaded_accounts_data_size)) = processing_result
                .processed_transaction()
                .map(|processed_tx| {
                    (
                        processed_tx.executed_units(),
                        processed_tx.loaded_accounts_data_size(),
                    )
                })
            else {
                transaction_costs.push(None);
                continue;
            };

            transaction_costs.push(Some(CostModel::calculate_cost_for_executed_transaction(
                tx,
                executed_units,
                loaded_accounts_data_size,
                &bank.feature_set,
            )));
        }

        transaction_costs
    }
```

**File:** transaction-context/src/transaction_accounts.rs (L297-326)
```rust
    pub(crate) fn update_accounts_resize_delta(
        &self,
        old_len: usize,
        new_len: usize,
    ) -> Result<(), InstructionError> {
        let accounts_resize_delta = self.resize_delta.get();
        self.resize_delta.set(
            accounts_resize_delta.saturating_add((new_len as i64).saturating_sub(old_len as i64)),
        );
        Ok(())
    }

    pub(crate) fn can_data_be_resized(
        &self,
        old_len: usize,
        new_len: usize,
    ) -> Result<(), InstructionError> {
        // The new length can not exceed the maximum permitted length
        if new_len > MAX_ACCOUNT_DATA_LEN as usize {
            return Err(InstructionError::InvalidRealloc);
        }
        // The resize can not exceed the per-transaction maximum
        let length_delta = (new_len as i64).saturating_sub(old_len as i64);
        if self.resize_delta.get().saturating_add(length_delta)
            > MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION
        {
            return Err(InstructionError::MaxAccountsDataAllocationsExceeded);
        }
        Ok(())
    }
```

**File:** transaction-context/src/lib.rs (L14-26)
```rust
pub const MAX_ACCOUNTS_PER_TRANSACTION: usize = 256;
// This is one less than MAX_ACCOUNTS_PER_TRANSACTION because
// one index is used as NON_DUP_MARKER in ABI v0 and v1.
pub const MAX_ACCOUNTS_PER_INSTRUCTION: usize = 255;
pub const MAX_INSTRUCTION_DATA_LEN: usize = 10 * 1024;
pub const MAX_ACCOUNT_DATA_LEN: u64 = 10 * 1024 * 1024;
// Note: With virtual_address_space_adjustments programs can grow accounts
// faster than they intend to, because the AccessViolationHandler might grow
// an account up to MAX_ACCOUNT_DATA_GROWTH_PER_INSTRUCTION at once.
pub const MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION: i64 = MAX_ACCOUNT_DATA_LEN as i64 * 2;
pub const MAX_ACCOUNT_DATA_GROWTH_PER_INSTRUCTION: usize = 10 * 1_024;
// Maximum cross-program invocation and instructions per transaction
pub const MAX_INSTRUCTION_TRACE_LENGTH: usize = 64;
```

**File:** cost-model/src/cost_tracker.rs (L288-293)
```rust
        let allocated_accounts_data_size =
            self.allocated_accounts_data_size + Saturating(tx_cost.allocated_accounts_data_size());

        if allocated_accounts_data_size.0 > self.limits.allocated_data_size {
            return Err(CostTrackerError::WouldExceedAccountDataBlockLimit);
        }
```

**File:** cost-model/src/block_cost_limits.rs (L35-37)
```rust
/// The maximum allowed size, in bytes, that accounts data can grow, per block.
/// This can also be thought of as the maximum size of new allocations per block.
pub const MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA: u64 = 100_000_000;
```
