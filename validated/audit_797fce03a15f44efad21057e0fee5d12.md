### Title
Fee-transfer entry-point failure aborts the entire block proposal instead of only rejecting the offending transaction - (File: `crates/apollo_batcher/src/block_builder.rs`)

### Summary
The M-6 report describes a class of bug where a critical, protective operation (`slash()`) internally invokes a sub-call to an external component (`_claimAndExit()`, iterating over plugins), and a revert in *any* one of those sub-calls causes the entire protective operation to fail, letting the bad actor escape punishment. The reachable analog in this sequencer is fee charging: `AccountTransaction::execute_raw` performs validation/execution and then calls `Self::handle_fee(...)?`, which in turn calls `execute_fee_transfer` — an ERC20 `transfer` call to the fee token contract with a fixed initial gas budget [1](#0-0) . If this sub-call fails (e.g. `AnnotatedEntryPointExecutionError` bubbled as `TransactionFeeError::ExecuteFeeTransferError`), the error propagates out of `execute_raw` as a hard `Err`, not as a normal "reverted transaction" result [2](#0-1) .

### Finding Description
Unlike ordinary Cairo execution failures — which are caught inside `run_revertible` and converted into a `ValidateExecuteCallInfo::new_reverted(...)` so the transaction is still included in the block with the sender's nonce bumped and (partial) fee charged [3](#0-2)  — the fee-transfer call performed in `handle_fee`/`execute_fee_transfer` happens *after* the revertible section and is not wrapped in the same recoverable handling. It executes on the live `state` (not a throwaway transactional state) with only a fixed `default_initial_gas_cost` budget [4](#0-3) , and any failure of this call (e.g. insufficient gas for the fee token's `transfer` logic, or any other execution error inside the token contract) is propagated with `?` all the way to `execute_raw`'s return type, producing an `Err(TransactionExecutionError)` rather than an `Ok(TransactionExecutionInfo)`.

This propagates to the `TransactionExecutor`, which reports it via `get_new_results()` as `Err(TransactionExecutorError::TransactionExecutionError(..))` for that transaction, as seen in the batcher's handling logic [5](#0-4) . Such per-transaction executor errors are converted by the block builder into `BlockBuilderError::FailOnError(FailOnErrorCause::TransactionFailed(..))` [6](#0-5) , which is a top-level failure of `build_block_inner()`. Any such error causes `build_block()` to abort the whole executor state for that proposal [7](#0-6) , and `proposal_status_from` maps `FailOnError` to `ProposalStatus::InvalidProposal`, discarding the entire block-building attempt rather than only excluding the one problematic transaction [8](#0-7) .

This mirrors the Telcoin bug class exactly: a subordinate/downstream call (fee-token `transfer`, analogous to `_claimAndExit`'s plugin claims) that is expected to reliably succeed can fail for reasons outside the protocol's core logic (fee-token implementation quirks, gas exhaustion), and that failure escalates from "this one transaction should be rejected/reverted" into "the entire batch/proposal operation fails."

### Impact Explanation
If a sender can construct or interact with a fee-token contract path such that the mandatory post-validation fee-transfer call fails (distinct from the already-handled "insufficient balance" case caught by `PostExecutionReport`), the resulting error is not a normal transaction revert but a hard executor error. This halts the in-progress block proposal for the proposer (and can similarly abort validation for validators receiving the same transaction), rather than simply excluding/rejecting the single transaction. Repeated triggering by an adversarial transaction sender could degrade the sequencer's ability to reliably build/validate blocks containing that transaction, a liveness/availability impact for block production — analogous to how the slashing bug let a bad actor block the intended remedial action by causing a hard failure in a should-not-fail sub-call.

### Likelihood Explanation
This requires a specific, narrower precondition than the Telcoin case: the sender's balance must be sufficient (so `PostExecutionReport`'s balance check passes) yet the actual `transfer` execution still fails for another reason (e.g., transient gas exhaustion under the fixed `default_initial_gas_cost` budget, or an execution-level bug/edge case in the entry-point invocation machinery). I could not fully confirm, within the available time and tool budget, whether such a failure is actually reachable in practice from a single transaction under the current fee-token/gas-budget setup, or whether upstream invariants (e.g., resource bound / fee bound checks) always guarantee this call cannot fail once balance is sufficient. This is a structural, not empirically demonstrated, weakness.

### Recommendation
Treat failures from `execute_fee_transfer`/`handle_fee` the same way ordinary execution failures are treated: catch the error inside `execute_raw` (or in `run_or_revert`/`handle_fee`) and convert it into a `TransactionExecutionInfo` with a `revert_error`, so a single misbehaving fee-transfer path rejects only that transaction instead of returning a hard `Err` that unwinds into `BlockBuilderError::FailOnError` and aborts the whole proposal.

### Proof of Concept
Not constructed — I was unable to verify, given the remaining investigation budget, whether the `execute_fee_transfer` call can be made to fail from a single external transaction while balance/resource-bound checks in `PostExecutionReport` still pass. A concrete PoC would need to: (1) confirm whether `default_initial_gas_cost` can be exhausted by a legitimate ERC20 fee-token `transfer` under some configuration, or find another path making `AnnotatedEntryPointExecutionError` surface from `execute_fee_transfer`, then (2) trace that error through `TransactionExecutor::execute`/`get_new_results` into `BlockBuilder::handle_executed_txs` to confirm it manifests as `FailOnErrorCause::TransactionFailed` and aborts the proposal as described above.

### Citations

**File:** crates/blockifier/src/transaction/account_transaction.rs (L550-591)
```rust
    fn execute_fee_transfer(
        state: &mut dyn State,
        tx_context: Arc<TransactionContext>,
        actual_fee: Fee,
    ) -> TransactionExecutionResult<CallInfo> {
        // The least significant 128 bits of the amount transferred.
        let lsb_amount = Felt::from(actual_fee.0);
        // The most significant 128 bits of the amount transferred.
        let msb_amount = Felt::ZERO;

        let TransactionContext { block_context, tx_info } = tx_context.as_ref();
        let storage_address = tx_context.fee_token_address();
        // The fee contains the cost of running this transfer, and the token contract is
        // well known to the sequencer, so there is no need to limit its run.
        let mut remaining_gas_for_fee_transfer =
            block_context.versioned_constants.os_constants.gas_costs.base.default_initial_gas_cost;
        let fee_transfer_call = CallEntryPoint {
            class_hash: None,
            code_address: None,
            entry_point_type: EntryPointType::External,
            entry_point_selector: selector_from_name(constants::TRANSFER_ENTRY_POINT_NAME),
            calldata: calldata![
                *block_context.block_info.sequencer_address.0.key(), // Recipient.
                lsb_amount,
                msb_amount
            ],
            storage_address,
            caller_address: tx_info.sender_address(),
            call_type: CallType::Call,

            initial_gas: remaining_gas_for_fee_transfer,
        };
        let mut context = EntryPointExecutionContext::new_invoke(
            tx_context,
            true,
            SierraGasRevertTracker::new(GasAmount(remaining_gas_for_fee_transfer)),
        );

        Ok(fee_transfer_call
            .execute(state, &mut context, &mut remaining_gas_for_fee_transfer)
            .map_err(|error| Box::new(TransactionFeeError::ExecuteFeeTransferError(error)))?)
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L766-841)
```rust
        match execution_result {
            Ok(execute_call_info) => {
                // When execution succeeded, calculate the actual required fee before committing the
                // transactional state. If max_fee is insufficient, revert the `run_execute` part.
                let tx_receipt = TransactionReceipt::from_account_tx(
                    self,
                    &tx_context,
                    &StateCache::squash_state_diff(
                        vec![
                            &validate_state_cache,
                            &execution_state.borrow_updated_state_cache()?.clone(),
                        ],
                        tx_context.block_context.versioned_constants.comprehensive_state_diff,
                    ),
                    CallInfo::summarize_many(
                        validate_call_info.iter().chain(execute_call_info.iter()),
                        &tx_context.block_context.versioned_constants,
                    ),
                    0,
                    GasAmount(0),
                );
                // Post-execution checks.
                let post_execution_report = PostExecutionReport::new(
                    &mut execution_state,
                    &tx_context,
                    &tx_receipt,
                    self.execution_flags.charge_fee,
                )?;
                match post_execution_report.error() {
                    Some(post_execution_error) => {
                        // Post-execution check failed. Revert the execution, compute the final fee
                        // to charge and recompute resources used (to be consistent with other
                        // revert case, compute resources by adding consumed execution steps to
                        // validation resources).
                        execution_state.abort();
                        let tx_receipt = TransactionReceipt {
                            fee: post_execution_report.recommended_fee(),
                            ..get_revert_receipt()
                        };
                        Ok(ValidateExecuteCallInfo::new_reverted(
                            validate_call_info,
                            post_execution_error.into(),
                            tx_receipt,
                        ))
                    }
                    None => {
                        // Post-execution check passed, commit the execution.
                        execution_state.commit();
                        Ok(ValidateExecuteCallInfo::new_accepted(
                            validate_call_info,
                            execute_call_info,
                            tx_receipt,
                        ))
                    }
                }
            }
            Err(execution_error) => {
                let revert_receipt = get_revert_receipt();
                // Error during execution. Revert, even if the error is sequencer-related.
                execution_state.abort();
                let post_execution_report = PostExecutionReport::new(
                    state,
                    &tx_context,
                    &revert_receipt,
                    self.execution_flags.charge_fee,
                )?;
                Ok(ValidateExecuteCallInfo::new_reverted(
                    validate_call_info,
                    gen_tx_execution_error_trace(&execution_error).into(),
                    TransactionReceipt {
                        fee: post_execution_report.recommended_fee(),
                        ..revert_receipt
                    },
                ))
            }
        }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L914-937)
```rust
        // Nonce and fee check should be done before running user code.
        self.perform_pre_validation_stage(state, &tx_context).map_err(Box::new)?;

        // Run validation and execution.
        let initial_gas = tx_context.initial_sierra_gas();
        let ValidateExecuteCallInfo {
            validate_call_info,
            execute_call_info,
            revert_error,
            final_cost:
                TransactionReceipt {
                    fee: final_fee,
                    da_gas: final_da_gas,
                    resources: final_resources,
                    gas: total_gas,
                },
        } = self.run_or_revert(state, &mut GasCounter::new(initial_gas), tx_context.clone())?;
        let fee_transfer_call_info = Self::handle_fee(
            state,
            tx_context,
            final_fee,
            self.execution_flags.charge_fee,
            concurrency_mode,
        )?;
```

**File:** crates/apollo_batcher/src/block_builder_test.rs (L1074-1077)
```rust
    helper.expect_get_new_results_with_results(vec![
        Err(TransactionExecutorError::StateError(StateError::OutOfRangeContractAddress)),
        Ok((execution_info(), StateMaps::default())),
    ]);
```

**File:** crates/apollo_batcher/src/block_builder.rs (L107-117)
```rust
#[derive(Debug, Error)]
pub enum FailOnErrorCause {
    #[error("Block is full")]
    BlockFull,
    #[error("Deadline has been reached")]
    DeadlineReached,
    #[error("Transaction failed: {0}")]
    TransactionFailed(BlockifierTransactionExecutorError),
    #[error("L1 Handler transaction validation failed: {0}")]
    L1HandlerTransactionValidationFailed(TransactionProviderError),
}
```

**File:** crates/apollo_batcher/src/block_builder.rs (L306-318)
```rust
    async fn build_block(&mut self) -> BlockBuilderResult<BlockExecutionArtifacts> {
        let res = self.build_block_inner().await;
        if res.is_err() {
            let executor = self.executor.clone();
            spawn_blocking(move || {
                let mut locked_executor = executor.blocking_lock();
                locked_executor.abort_block();
            })
            .await
            .expect("Aborting block should succeed.");
        }
        res
    }
```

**File:** crates/apollo_batcher/src/utils.rs (L68-81)
```rust
// Return the appropriate ProposalStatus for a given ProposalError.
pub(crate) fn proposal_status_from(
    block_builder_error: Arc<BlockBuilderError>,
) -> BatcherResult<ProposalStatus> {
    match block_builder_error.as_ref() {
        // FailOnError means the proposal either failed due to bad input (e.g. invalid
        // transactions), or couldn't finish in time.
        BlockBuilderError::FailOnError(err) => Ok(ProposalStatus::InvalidProposal(err.to_string())),
        BlockBuilderError::Aborted => Err(BatcherError::ProposalAborted),
        _ => {
            tracing::error!("Unexpected error: {}", block_builder_error);
            Err(BatcherError::InternalError)
        }
    }
```
