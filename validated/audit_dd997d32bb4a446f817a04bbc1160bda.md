### Title
Panic via `.expect()` on `execute_call_info` in RPC transaction trace conversion when execution succeeds without an execute call info - ([File: crates/apollo_rpc_execution/src/objects.rs])

### Summary
`apollo_rpc_execution` converts a `TransactionExecutionInfo` produced by `blockifier` into an RPC-facing trace object. For `Invoke` and `DeployAccount` transactions it uses `.expect(...)` on `execute_call_info`, assuming that whenever the transaction did not revert, `execute_call_info` must be `Some`. This mirrors the reported nord bug class: an `unwrap()`/`expect()` on an `Option` that is documented as "must be present unless the execution failed," but the invariant is not actually enforced everywhere in the execution engine.

### Finding Description
`TransactionExecutionInfo` is documented as having `execute_call_info: Option<CallInfo>` that is `None` "for `Declare`" transactions only [1](#0-0) . Based on that assumption, the RPC trace-conversion code unconditionally unwraps it for `Invoke` and `DeployAccount` traces whenever `revert_error` is `None`: [2](#0-1) [3](#0-2) 

This is structurally identical to the reported nord issue: a receipt-conversion routine assumes an `Option` field is populated whenever the operation is not marked as failed, and calls `.unwrap()`/`.expect()` on it instead of matching/erroring gracefully. If any execution path in `blockifier`'s `AccountTransaction` (e.g., `run_non_revertible`, flows with `execution_flags.validate` disabled, `only_query` execution, or future/edge-case flows that skip populating `execute_call_info` on success) can produce a `TransactionExecutionInfo` with `revert_error: None` and `execute_call_info: None` for an `Invoke` or `DeployAccount` transaction, this `.expect()` will panic the process.

### Impact Explanation
This code path is exercised in the read/trace API (`apollo_rpc_execution`), which converts execution results for `starknet_traceTransaction`, `starknet_traceBlockTransactions`, and `starknet_simulateTransactions` RPC calls — endpoints reachable by any unprivileged RPC client submitting or simulating a transaction. A panic here would crash the RPC-execution service thread/process handling the request, resulting in denial-of-service for the node's read/trace API, matching the "network unable to confirm/serve transactions" impact class (loss of availability for the affected node, and if the panic occurs during block-level tracing rather than an isolated query, wider disruption of node function is possible).

### Likelihood Explanation
The likelihood of actually triggering this in the current codebase could not be conclusively confirmed within the available investigation time. All examined `run_execute` implementations for `InvokeTransaction`, `DeployAccountTransaction`, and the `run_non_revertible`/`run_revertible` flows in `account_transaction.rs` populate `execute_call_info` as `Some` on any non-reverted outcome in the paths inspected [4](#0-3) [5](#0-4) . I was not able to fully audit every execution flag combination (e.g., interactions between `execution_flags.validate = false`, `only_query`, and fee-charging flags) before running out of iterations, so I cannot rule out an edge case that produces `execute_call_info: None` with `revert_error: None`. Given this uncertainty and that no concrete reachable trigger was proven, this should be treated as a **defensive-coding gap** rather than a confirmed exploitable vulnerability.

### Recommendation
Replace the `.expect(...)` calls on `execute_call_info` in `crates/apollo_rpc_execution/src/objects.rs` (`TryFrom<TransactionExecutionInfo> for InvokeTransactionTrace` and `for DeployAccountTransactionTrace`) with a `match`/`Result`-returning check that surfaces a descriptive `ExecutionError` instead of panicking, consistent with how `validate_call_info` and `fee_transfer_call_info` are already handled as genuine `Option`s in the same functions. This removes reliance on an invariant that is not type-enforced and avoids crashing the RPC-execution service on any future or currently-unverified code path that could produce this combination.

### Proof of Concept
No concrete reachable trigger was identified in the time available; the analysis is based on structural analogy (an `.expect()`/`unwrap()` on an `Option` assumed non-empty by comment/doc convention rather than by type or by exhaustive verification across all execution flag combinations) to the reported nord `book.rs`/`view.rs` bug. A background engineering session with full test-execution capability would be needed to enumerate `ExecutionFlags` combinations (`validate=false`, `charge_fee=false`, `only_query`, reverts disabled) against `Invoke`/`DeployAccount` transactions to confirm whether `execute_call_info: None` with `revert_error: None` is actually reachable through `apollo_rpc_execution`'s simulate/trace RPC entry points.

### Citations

**File:** crates/blockifier/src/transaction/objects.rs (L216-220)
```rust
    pub validate_call_info: Option<CallInfo>,
    /// Transaction execution call info; [None] for `Declare`.
    pub execute_call_info: Option<CallInfo>,
    /// Fee transfer call info; [None] for `L1Handler`.
    pub fee_transfer_call_info: Option<CallInfo>,
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L126-138)
```rust
        let execute_invocation = match transaction_execution_info.revert_error {
            Some(revert_error) => {
                FunctionInvocationResult::Err(RevertReason::RevertReason(revert_error.to_string()))
            }
            None => FunctionInvocationResult::Ok(
                (
                    transaction_execution_info
                        .execute_call_info
                        .expect("Invoke transaction execution should contain execute_call_info."),
                    transaction_execution_info.receipt.da_gas,
                )
                    .try_into()?,
            ),
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L239-246)
```rust
            constructor_invocation: (
                transaction_execution_info.execute_call_info.expect(
                    "Deploy account execution should contain execute_call_info (the constructor \
                     call info).",
                ),
                transaction_execution_info.receipt.da_gas,
            )
                .try_into()?,
```

**File:** crates/blockifier/src/transaction/transactions.rs (L298-336)
```rust
impl<S: State> Executable<S> for InvokeTransaction {
    fn run_execute(
        &self,
        state: &mut S,
        context: &mut EntryPointExecutionContext,
        remaining_gas: &mut u64,
    ) -> TransactionExecutionResult<Option<CallInfo>> {
        let entry_point_selector = match &self.tx {
            starknet_api::transaction::InvokeTransaction::V0(tx) => tx.entry_point_selector,
            starknet_api::transaction::InvokeTransaction::V1(_)
            | starknet_api::transaction::InvokeTransaction::V3(_) => {
                selector_from_name(constants::EXECUTE_ENTRY_POINT_NAME)
            }
        };
        let storage_address = context.tx_context.tx_info.sender_address();
        let class_hash = state.get_class_hash_at(storage_address)?;
        let execute_call = CallEntryPoint {
            entry_point_type: EntryPointType::External,
            entry_point_selector,
            calldata: self.calldata(),
            class_hash: None,
            code_address: None,
            storage_address,
            caller_address: ContractAddress::default(),
            call_type: CallType::Call,
            initial_gas: *remaining_gas,
        };

        let call_info =
            execute_call.non_reverting_execute(state, context, remaining_gas).map_err(|error| {
                TransactionExecutionError::ExecutionError {
                    error: Box::new(error),
                    class_hash,
                    storage_address,
                    selector: entry_point_selector,
                }
            })?;
        Ok(Some(call_info))
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L645-711)
```rust
    fn run_non_revertible<S: StateReader>(
        &self,
        state: &mut TransactionalState<'_, S>,
        tx_context: Arc<TransactionContext>,
        remaining_gas: &mut GasCounter,
    ) -> TransactionExecutionResult<ValidateExecuteCallInfo> {
        let validate_call_info: Option<CallInfo>;
        let execute_call_info: Option<CallInfo>;
        if matches!(&self.tx, Transaction::DeployAccount(_)) {
            // Handle `DeployAccount` transactions separately, due to different order of things.
            // Also, the execution context required for the `DeployAccount` execute phase is
            // validation context.
            let mut execution_context = EntryPointExecutionContext::new_validate(
                tx_context.clone(),
                self.execution_flags.charge_fee,
                // TODO(Dori): Reduce code dup (the gas usage limit is computed in run_execute).
                // We initialize the revert gas tracker here for completeness - the value will not
                // be used, as this tx is non-revertible.
                SierraGasRevertTracker::new(GasAmount(
                    remaining_gas
                        .limit_usage(tx_context.sierra_gas_limit(&ExecutionMode::Validate)),
                )),
            );
            execute_call_info = self.run_execute(state, &mut execution_context, remaining_gas)?;
            validate_call_info = self.validate_tx(state, tx_context.clone(), remaining_gas)?;
        } else {
            validate_call_info = self.validate_tx(state, tx_context.clone(), remaining_gas)?;
            let mut execution_context = EntryPointExecutionContext::new_invoke(
                tx_context.clone(),
                self.execution_flags.charge_fee,
                // TODO(Dori): Reduce code dup (the gas usage limit is computed in run_execute).
                // We initialize the revert gas tracker here for completeness - the value will not
                // be used, as this tx is non-revertible.
                SierraGasRevertTracker::new(GasAmount(
                    remaining_gas.limit_usage(tx_context.sierra_gas_limit(&ExecutionMode::Execute)),
                )),
            );
            execute_call_info = self.run_execute(state, &mut execution_context, remaining_gas)?;
        }

        let tx_receipt = TransactionReceipt::from_account_tx(
            self,
            &tx_context,
            &state.to_state_diff()?,
            CallInfo::summarize_many(
                validate_call_info.iter().chain(execute_call_info.iter()),
                &tx_context.block_context.versioned_constants,
            ),
            0,
            GasAmount(0),
        );

        let post_execution_report = PostExecutionReport::new(
            state,
            &tx_context,
            &tx_receipt,
            self.execution_flags.charge_fee,
        )?;
        match post_execution_report.error() {
            Some(error) => Err(error.into()),
            None => Ok(ValidateExecuteCallInfo::new_accepted(
                validate_call_info,
                execute_call_info,
                tx_receipt,
            )),
        }
    }
```
