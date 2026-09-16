### Title
Fee-transfer entry point call result is never checked for execution failure, causing the sequencer to record fees as collected even when the underlying ERC20 transfer reverted - (File: crates/blockifier/src/transaction/account_transaction.rs)

### Summary
`AccountTransaction::execute_fee_transfer` invokes the fee-token `transfer` entry point via `CallEntryPoint::execute(...)` but never inspects `CallInfo.execution.failed` on the returned `CallInfo`. Unlike other single-call flows in the codebase that explicitly guard against a failed/panicked callee (`non_reverting_execute`), the fee-transfer path accepts any `Ok(call_info)` — including one where `execution.failed == true` — as a successful fee payment.

### Finding Description
`execute_fee_transfer` builds a `CallEntryPoint` for the ERC20 `transfer` selector and executes it with the plain `execute` method, propagating the `CallInfo` unconditionally through `handle_fee`: [1](#0-0) 

`execute` internally delegates to `execute_entry_point_call_wrapper`, which — when `enable_reverts` is set (the standard mode) — returns `Ok(call_info)` even if `call_info.execution.failed` is `true`; it only converts a failure into an `Err` when reverts are disabled: [2](#0-1) 

Contrast this with `non_reverting_execute`, defined right next to `execute` in the same file, which explicitly checks `call_info.execution.failed` and turns it into an `EntryPointExecutionError::ExecutionFailed`: [3](#0-2) 

`execute_fee_transfer` does not use this "non-reverting" variant, so a reverted/panicked ERC20 `transfer` call (e.g. due to a hook, denylist, pause flag, or any revert condition in the fee-token contract beyond the balance check already done in pre-validation) is silently accepted: [4](#0-3) 

The resulting `fee_transfer_call_info` (with `execution.failed = true`) is stored as-is in `TransactionExecutionInfo`, and the transaction is still reported/committed with `receipt.fee = final_fee` as if the sequencer was actually paid: [5](#0-4) 

### Impact Explanation
Because a reverted entry point call rolls back the storage writes performed during that call (the ERC20 balance movement never actually happens), while the transaction is nonetheless treated as fully accepted with a recorded, nonzero fee, this creates a divergence between the accounted state (fee "collected", included in the block's fee receipt/gas accounting) and the actual token balances (sequencer's fee-token balance unchanged, sender's balance unchanged). This is a direct loss of sequencer rewards: fee-token value that should have moved to the sequencer address never does, yet the block/transaction bookkeeping proceeds as though it did. It can also produce commitment/state inconsistencies across honest sequencer nodes if any of them handle this edge case differently or if downstream fee aggregation logic (e.g., `add_fee_to_sequencer_balance` in concurrency mode) assumes the transfer always succeeds.

### Likelihood Explanation
The fee-token contract address is governance-controlled and Cairo-programmable; while the "happy path" (a standard ERC20 with only a balance check) rarely panics beyond the balance check already performed in pre-validation, any legitimate variance in the fee-token contract's `transfer` implementation (hooks, denylists, pausability, reentrancy guards, future upgrades) can cause the call to fail for reasons unrelated to balance. Because the check is entirely absent, this is reachable by any account transaction whose fee-token `transfer` entry point reverts for any reason after passing the sender-balance pre-check, making it a plausible, transaction-triggerable path with no special privileges required.

### Recommendation
Use `non_reverting_execute` (or explicitly check `call_info.execution.failed`) in `execute_fee_transfer`, and propagate a `TransactionFeeError` if the fee-transfer call fails, consistent with the existing pattern used elsewhere in the entry-point execution code (`non_reverting_execute`) so failed fee transfers cause the transaction to be rejected/reverted rather than silently accounted as paid.

### Proof of Concept
1. Configure (or have block_context reference) a fee-token contract whose `transfer` function reverts for the sequencer-recipient address under some condition unrelated to the sender's balance (e.g., a denylist/pause flag, or any Cairo1 panic reachable post-balance-check).
2. Submit a normal account transaction (invoke/declare/deploy_account) with sufficient balance to pass pre-validation.
3. During `handle_fee` → `execute_fee_transfer`, the `transfer` call panics/reverts; `execute` returns `Ok(call_info)` with `call_info.execution.failed = true` because reverts are enabled.
4. `AccountTransaction::execute_raw` stores this failed `call_info` as `fee_transfer_call_info` and returns `TransactionExecutionInfo` with `receipt.fee = final_fee` and no `revert_error`, meaning the transaction is committed as accepted and "having paid" the fee even though the sequencer's fee-token balance was not actually incremented.

### Citations

**File:** crates/blockifier/src/transaction/account_transaction.rs (L526-548)
```rust
    fn handle_fee<S: StateReader>(
        state: &mut TransactionalState<'_, S>,
        tx_context: Arc<TransactionContext>,
        actual_fee: Fee,
        charge_fee: bool,
        concurrency_mode: bool,
    ) -> TransactionExecutionResult<Option<CallInfo>> {
        if !charge_fee || actual_fee == Fee(0) {
            // Fee charging is not enforced in some tests.
            // TODO(Yoni): consider setting the actual fee to zero when the flag is off.
            return Ok(None);
        }

        Self::assert_actual_fee_in_bounds(&tx_context, actual_fee);

        let fee_transfer_call_info = if concurrency_mode && !tx_context.is_sequencer_the_sender() {
            Self::concurrency_execute_fee_transfer(state, tx_context, actual_fee)?
        } else {
            Self::execute_fee_transfer(state, tx_context, actual_fee)?
        };

        Ok(Some(fee_transfer_call_info))
    }
```

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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L914-951)
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

        let tx_execution_info = TransactionExecutionInfo {
            validate_call_info,
            execute_call_info,
            fee_transfer_call_info,
            receipt: TransactionReceipt {
                fee: final_fee,
                da_gas: final_da_gas,
                resources: final_resources,
                gas: total_gas,
            },
            revert_error,
        };
        Ok(tx_execution_info)
```

**File:** crates/blockifier/src/execution/execution_utils.rs (L77-91)
```rust
    match res {
        Ok(call_info) => {
            if call_info.execution.failed && !context.versioned_constants().enable_reverts {
                // Reverts are disabled.
                return Err(EntryPointExecutionError::ExecutionFailed {
                    error_trace: extract_trailing_cairo1_revert_trace(
                        &call_info,
                        Cairo1RevertHeader::Execution,
                    ),
                }
                .annotated(current_tracked_resource, strip_vm_frames));
            }
            update_remaining_gas(remaining_gas, &call_info);
            Ok(call_info)
        }
```

**File:** crates/blockifier/src/execution/entry_point.rs (L240-268)
```rust
    /// Similar to `execute`, but returns an error if the outer call is reverted.
    pub fn non_reverting_execute(
        self,
        state: &mut dyn State,
        context: &mut EntryPointExecutionContext,
        remaining_gas: &mut u64,
    ) -> EntryPointExecutionResult<CallInfo> {
        let execution_result = self.execute(state, context, remaining_gas);
        if let Ok(call_info) = &execution_result {
            // Update revert gas tracking (for completeness - value will not be used unless the tx
            // is reverted).
            context.sierra_gas_revert_tracker.update_with_next_remaining_gas(
                call_info.tracked_resource,
                GasAmount(*remaining_gas),
            );
            // If the execution of the outer call failed, revert the transction.
            if call_info.execution.failed {
                return Err(EntryPointExecutionError::ExecutionFailed {
                    error_trace: extract_trailing_cairo1_revert_trace(
                        call_info,
                        Cairo1RevertHeader::Execution,
                    ),
                }
                .annotated(
                    call_info.tracked_resource,
                    context.versioned_constants().strip_vm_frames_in_sierra_gas,
                ));
            }
        }
```
