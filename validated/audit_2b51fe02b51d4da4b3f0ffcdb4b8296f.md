### Title
Cost-tracker never releases `allocated_accounts_data_size` budget for committed-but-failed `CreateAccount`/`CreateAccountAllowPrefund` instructions, allowing cheap exhaustion of the block-wide accounts-data-size limit - ([File: cost-model/src/cost_tracker.rs])

### Summary
`CostModel::calculate_allocated_accounts_data_size` only detects *statically* determinable failures (e.g. `space > MAX_PERMITTED_DATA_LENGTH`) and otherwise charges the full requested `space` against the transaction's `allocated_accounts_data_size`, which is reserved in `CostTracker::try_add` against the block-wide `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` limit. When such a transaction is subsequently *committed* to the block (fee charged) but its `CreateAccount`/`CreateAccountAllowPrefund` instruction fails at runtime (e.g. `InstructionError::InsufficientFunds` for rent-exemption), `QosService::remove_or_update_costs` routes it through `CostTracker::update_execution_cost`, which only adjusts execution/loaded-accounts-data-size cost and never touches `allocated_accounts_data_size`. The reserved allocation is only released via `CostTracker::remove` when the transaction is `NotCommitted` (dropped before landing), not when it lands with a failed instruction. This lets a cheap, repeatable sequence of guaranteed-to-fail large-`space` transactions permanently consume the slot's account-data-size budget.

### Finding Description
- `CostModel::calculate_account_data_size_on_deserialized_system_instruction` (`cost-model/src/cost_model.rs:203-240`) treats `CreateAccount`/`CreateAccountAllowPrefund` with `space <= MAX_PERMITTED_DATA_LENGTH` as `SystemProgramAccountAllocation::Some(space)` regardless of whether the payer/funding account actually has enough lamports for rent-exemption. It cannot know about, and does not attempt to simulate, execution-time failures such as insufficient funds. [1](#0-0) 
- `calculate_allocated_accounts_data_size` sums these attempted allocations into `tx_attempted_allocation_size`, which becomes `TransactionCost::allocated_accounts_data_size`. [2](#0-1) 
- `CostTracker::try_add` reserves this amount against the block-wide `allocated_data_size` limit (default `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA`) before the transaction even executes. [3](#0-2) 
- After execution, `QosService::remove_or_update_costs` differentiates only between `Committed` (transaction landed in the block, fee charged, regardless of whether the inner instruction errored) and `NotCommitted` (transaction never landed, e.g. dropped by PoH). Only the `NotCommitted` branch calls `cost_tracker.remove(tx_cost)`, which fully reverses `allocated_accounts_data_size`. The `Committed` branch calls `update_execution_cost` instead. [4](#0-3) 
- `CostTracker::update_execution_cost` only adjusts `programs_execution_cost`/`loaded_accounts_data_size_cost` via `add_transaction_execution_cost`/`sub_transaction_execution_cost`; it never modifies `allocated_accounts_data_size`. [5](#0-4) 
- Only `remove_transaction_cost` (called from `remove`, used solely for `NotCommitted` transactions) decrements `allocated_accounts_data_size`. [6](#0-5) 

Consequently, a transaction that lands in the block (fee-paid, "Committed") but whose `CreateAccount`/`CreateAccountAllowPrefund` instruction fails at execution (e.g. `InstructionError::InsufficientFunds` because the funding account cannot cover rent-exemption for the requested `space`) keeps its full requested `space` permanently counted against the block's `allocated_data_size` budget for the remainder of the slot, even though no account data was ever actually allocated or persisted.

### Impact Explanation
This is a liveness/availability degradation: an attacker can submit a handful of cheap, guaranteed-to-fail `CreateAccount`/`CreateAccountAllowPrefund` transactions (each requesting `space` near `MAX_PERMITTED_DATA_LENGTH`, funded with lamports insufficient for rent-exemption) to exhaust the block-wide `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` budget tracked in `CostTracker`. Because `MAX_PERMITTED_DATA_LENGTH` is a large fraction of `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA`, only a small number of such transactions are needed to fill the budget for the rest of the slot, after which all legitimate transactions attempting large account allocations are rejected with `WouldExceedAccountDataBlockLimit` for the remainder of that slot. This matches the "denial of service / degraded liveness" bounty category rather than fund theft or consensus divergence.

### Likelihood Explanation
- Preconditions: attacker needs only an unprivileged, minimally-funded keypair (enough to pay the base transaction fee) and knowledge of the `CreateAccount`/`CreateAccountAllowPrefund` layouts — no special privileges, no validator/network access.
- Cost: each attack transaction costs only the standard signature/base fee (no rent, no actual allocation, since execution fails and all account state changes are rolled back).
- Feasibility/repeatability: fully client-side constructible and repeatable every slot, since the `CostTracker` budget resets per bank/slot. No sanitization, signer/writable check, feature gate, or metering step currently distinguishes "will fail due to insufficient funds" from "will succeed" at the cost-tracker admission stage, and post-execution cost adjustment does not correct for this specific field.

### Recommendation
Reconcile `allocated_accounts_data_size` after execution the same way `programs_execution_cost`/`loaded_accounts_data_size_cost` are reconciled: when a committed transaction's execution result shows the account-allocating instruction did not succeed (or the account data size delta actually persisted is smaller than requested), subtract the difference between the requested and actually-realized allocation from `CostTracker.allocated_accounts_data_size` in `update_execution_cost` (or a dedicated adjustment call), using the already-tracked `AccountsDeltas`/`accounts_resize_delta` from `TransactionExecutionDetails`. Alternatively, treat any transaction whose overall status is `Err` (not just `NotCommitted`) as fully removable from the `allocated_accounts_data_size` budget.

### Proof of Concept
Integration test plan (bank/SVM level, extending existing `qos_service.rs`/`cost_tracker.rs` test patterns):
1. Create a `Bank` with default `CostTrackerLimits` (or a small custom `allocated_data_size` limit for determinism).
2. Build N transactions, each with a fresh, minimally-funded fee payer and a single `SystemInstruction::CreateAccount` (or `CreateAccountAllowPrefund` with feature enabled) instruction requesting `space = MAX_PERMITTED_DATA_LENGTH - 1` and `lamports` insufficient for rent-exemption of that space.
3. Run these through `QosService::select_transactions_per_cost` to admit them into the `CostTracker` (asserting they are all `Ok`, i.e., admitted based on requested size).
4. Execute the batch via the bank; assert each transaction lands as `CommitTransactionDetails::Committed` with an execution error (`InstructionError::InsufficientFunds` or equivalent), i.e., the transaction is committed (fee paid) despite the instruction failing.
5. Call `QosService::remove_or_update_costs` with the `Committed` statuses and assert that `bank.read_cost_tracker().unwrap().stats().allocated_accounts_data_size` still equals the sum of the (failed) requested allocation sizes rather than 0.
6. Submit one more legitimate large `CreateAccount` transaction (properly funded, requesting a modest `space`) and assert it is rejected with `CostTrackerError::WouldExceedAccountDataBlockLimit` / `TransactionError::WouldExceedAccountDataBlockLimit`, demonstrating the block's allocation budget has been exhausted by transactions whose allocations never materialized.

### Citations

**File:** cost-model/src/cost_model.rs (L203-225)
```rust
    fn calculate_account_data_size_on_deserialized_system_instruction(
        instruction: SystemInstruction,
        feature_set: &FeatureSet,
    ) -> SystemProgramAccountAllocation {
        let validate_space = |space: u64| {
            if space > MAX_PERMITTED_DATA_LENGTH {
                SystemProgramAccountAllocation::Failed
            } else {
                SystemProgramAccountAllocation::Some(space)
            }
        };

        match instruction {
            SystemInstruction::CreateAccount { space, .. }
            | SystemInstruction::CreateAccountWithSeed { space, .. }
            | SystemInstruction::Allocate { space }
            | SystemInstruction::AllocateWithSeed { space, .. } => validate_space(space),
            SystemInstruction::CreateAccountAllowPrefund { space, .. } => {
                if !feature_set.snapshot().create_account_allow_prefund {
                    return SystemProgramAccountAllocation::Failed;
                }
                validate_space(space)
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

**File:** cost-model/src/cost_tracker.rs (L220-225)
```rust
        let allocated_accounts_data_size =
            self.allocated_accounts_data_size + Saturating(tx_cost.allocated_accounts_data_size());

        if allocated_accounts_data_size.0 > self.limits.allocated_data_size {
            return Err(CostTrackerError::WouldExceedAccountDataBlockLimit);
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

**File:** cost-model/src/cost_tracker.rs (L378-389)
```rust
    fn remove_transaction_cost(&mut self, tx_cost: &TransactionCost<impl TransactionWithMeta>) {
        let cost = tx_cost.sum();
        self.sub_transaction_execution_cost(tx_cost, cost);
        self.allocated_accounts_data_size -= tx_cost.allocated_accounts_data_size();
        self.transaction_count -= 1;
        self.transaction_signature_count -= tx_cost.num_transaction_signatures();
        self.secp256k1_instruction_signature_count -=
            tx_cost.num_secp256k1_instruction_signatures();
        self.ed25519_instruction_signature_count -= tx_cost.num_ed25519_instruction_signatures();
        self.secp256r1_instruction_signature_count -=
            tx_cost.num_secp256r1_instruction_signatures();
    }
```

**File:** core/src/banking_stage/qos_service.rs (L152-191)
```rust
    /// For recorded transactions, remove units reserved by uncommitted transaction, or update
    /// units for committed transactions.
    fn remove_or_update_recorded_transaction_costs<'a, Tx: TransactionWithMeta + 'a>(
        transaction_cost_results: impl Iterator<Item = &'a transaction::Result<TransactionCost<'a, Tx>>>,
        transaction_committed_status: &Vec<CommitTransactionDetails>,
        bank: &Bank,
    ) {
        let mut cost_tracker = bank.write_cost_tracker().unwrap();
        let mut num_included = 0;
        transaction_cost_results
            .zip(transaction_committed_status)
            .for_each(|(tx_cost, transaction_committed_details)| {
                // Only transactions that the qos service included have to be
                // checked for update
                if let Ok(tx_cost) = tx_cost {
                    num_included += 1;
                    match transaction_committed_details {
                        CommitTransactionDetails::Committed {
                            compute_units,
                            loaded_accounts_data_size,
                            result: _,
                            fee_payer_post_balance: _,
                        } => {
                            cost_tracker.update_execution_cost(
                                tx_cost,
                                *compute_units,
                                CostModel::calculate_loaded_accounts_data_size_cost(
                                    *loaded_accounts_data_size,
                                    &bank.feature_set,
                                ),
                            );
                        }
                        CommitTransactionDetails::NotCommitted(_err) => {
                            cost_tracker.remove(tx_cost);
                        }
                    }
                }
            });
        cost_tracker.sub_transactions_in_flight(num_included);
    }
```
