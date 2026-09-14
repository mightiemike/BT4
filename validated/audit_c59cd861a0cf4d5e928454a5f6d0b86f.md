No vulnerability found for this question.

The SurrealDB advisory is a permission-model bug specific to document-database field-level SELECT permissions being bypassed through verbose arithmetic/`extend` error messages during UPDATE operations. This class of bug requires a system where the same authenticated principal can have different levels of read access to different fields of the same record, and where an unrelated operation's error path embeds the raw hidden value.

nearcore has no equivalent concept: there is no field-level SELECT/UPDATE permission split on account or contract storage. Contract storage keys are either fully opaque bytes without any built-in field-visibility ACL, and any value returned in a `FunctionCallError`, `HostError::GuestPanic`, or query error originates either from the contract's own logic (the contract controls what it panics with) or from generic account/access-key metadata that is not access-controlled at a sub-field level in the first place.

I reviewed the relevant error-reporting paths — `near_primitives::errors::HostError` display implementation [1](#0-0) , the `FunctionCallError`/`RuntimeError` construction in `runtime/runtime/src/function_call.rs` [2](#0-1) , and the JSON-RPC `QueryError`/`RpcQueryError` conversions [3](#0-2)  — and none of them expose a scenario where an unprivileged caller can trigger an error that echoes a value they are otherwise denied read access to via a separate, narrower permission grant. There is no reachable analog matching the required "unauthorized value movement, supply inflation, fee/gas bypass, state-root divergence, invalid state transition, receipt loss/duplication, frozen funds, or halt" impact criteria.

### Citations

**File:** runtime/near-vm-runner/src/logic/errors.rs (L532-545)
```rust
impl std::fmt::Display for HostError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> Result<(), std::fmt::Error> {
        use HostError::*;
        match self {
            BadUTF8 => write!(f, "String encoding is bad UTF-8 sequence."),
            BadUTF16 => write!(f, "String encoding is bad UTF-16 sequence."),
            GasExceeded => write!(f, "Exceeded the prepaid gas."),
            GasLimitExceeded => {
                write!(f, "Exceeded the maximum amount of gas allowed to burn per contract.")
            }
            BalanceExceeded => write!(f, "Exceeded the account balance."),
            EmptyMethodName => write!(f, "Tried to call an empty method name."),
            GuestPanic { panic_msg } => write!(f, "Smart contract panicked: {}", panic_msg),
            IntegerOverflow => write!(f, "Integer overflow."),
```

**File:** runtime/runtime/src/function_call.rs (L280-342)
```rust
    near_vm_runner::reset_metrics();
    let result = near_vm_runner::run(contract, runtime_ext, &context, Arc::clone(&config.fees));
    near_vm_runner::report_metrics(apply_state.shard_id, &apply_state.apply_reason.to_string());

    // There are many specific errors that the runtime can encounter.
    // Some can be translated to the more general `RuntimeError`, which allows to pass
    // the error up to the caller. For all other cases, panicking here is better
    // than leaking the exact details further up.
    // Note that this does not include errors caused by user code / input, those are
    // stored in outcome.aborted.
    let mut outcome = match result {
        Err(VMRunnerError::ContractCodeNotPresent) => {
            if runtime_ext.account().contract().is_some() {
                debug_assert!(
                    apply_state.apply_reason != ApplyChunkReason::UpdateTrackedShard,
                    "inconsistent state: contract code is missing from the trie, but the account has a non-empty contract"
                );

                // A missing body for an account that commits to a code hash is
                // witness incompleteness, not an execution result. Fail like any
                // other missing witness value rather than treating it as no-op.
                if apply_state.apply_reason == ApplyChunkReason::ValidateChunkStateWitness {
                    return Err(StorageError::MissingTrieValue(MissingTrieValue {
                        context: MissingTrieValueContext::TrieMemoryPartialStorage,
                        hash: contract_code_hash,
                    })
                    .into());
                }
            }
            let error = FunctionCallError::CompilationError(CompilationError::CodeDoesNotExist {
                account_id: account_id.as_str().into(),
            });
            return Ok(VMOutcome::nop_outcome(error));
        }
        Err(VMRunnerError::ExternalError(any_err)) => {
            let err: ExternalError =
                any_err.downcast().expect("Downcasting AnyError should not fail");
            return Err(match err {
                ExternalError::StorageError(err) => err.into(),
                ExternalError::ValidatorError(err) => RuntimeError::ValidatorError(err),
            });
        }
        Err(VMRunnerError::InconsistentStateError(
            err @ InconsistentStateError::IntegerOverflow,
        )) => return Err(StorageError::StorageInconsistentState(err.to_string()).into()),
        Err(VMRunnerError::CacheError(err)) => {
            metrics::FUNCTION_CALL_PROCESSED_CACHE_ERRORS
                .with_label_values::<&str>(&[(&err).into()])
                .inc();
            return Err(StorageError::StorageInconsistentState(err.to_string()).into());
        }
        Err(VMRunnerError::LoadingError(msg)) => {
            return Ok(VMOutcome::nop_outcome(FunctionCallError::LoadingError { msg }));
        }
        Err(VMRunnerError::Nondeterministic(msg)) => {
            panic!("Contract runner returned non-deterministic error '{}', aborting", msg)
        }
        Err(VMRunnerError::WasmUnknownError { debug_message }) => {
            panic!("Wasmer returned unknown message: {}", debug_message)
        }
        Ok(r) => r,
    };

```

**File:** chain/jsonrpc-primitives/src/types/call_function.rs (L49-57)
```rust
    #[error("Function call returned an error: {vm_error:?}")]
    ContractExecutionError {
        vm_error: near_primitives::errors::FunctionCallError,
        block_height: near_primitives::types::BlockHeight,
        block_hash: near_primitives::hash::CryptoHash,
    },
    #[error("The node reached its limits. Try again later. More details: {error_message}")]
    InternalError { error_message: String },
}
```
