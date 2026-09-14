### Title
Unhandled `EpochError` from `validator_stake`/`validator_total_stake` host-function calls escalates to a fatal `RuntimeError`/chunk-apply failure instead of a graceful action failure - (File: `runtime/runtime/src/ext.rs`, `chain/chain/src/runtime/mod.rs`)

### Summary
The external report describes a Chainlink adapter that calls an external price registry without a try/catch, so a revert or block by the oracle propagates unhandled and DoSes `getPrice`. The nearcore analog is the `validator_stake`/`validator_total_stake` host functions, reachable from any deployed contract via a normal `FunctionCall` transaction (`ext_validator_stake`), which query the epoch manager (`EpochInfoProvider`, functioning as the "external oracle" for validator/epoch data) without converting a lookup failure into a normal, per-action `ActionError`. Instead, any `EpochError` returned by the epoch manager is wrapped as `ExternalError::ValidatorError` and ultimately surfaces as `RuntimeError::ValidatorError`, which — unlike ordinary contract-triggered failures — is *not* absorbed as a failed receipt/action but bubbles out of `Runtime::apply` as a hard `Err`, which chunk application propagates as a fatal `Error` rather than a `Failure(ActionError)` outcome.

### Finding Description
- Any unprivileged account can deploy a contract and call the `validator_stake`/`validator_total_stake` host functions via a plain `FunctionCall` action, as demonstrated in the existing test `test_validator_stake_host_function` (`integration-tests/src/tests/client/process_blocks.rs:3364-3401`), which sends a `FunctionCall` with `method_name: "ext_validator_stake"` from an ordinary account.
- Inside the runtime, this host call is served by `RuntimeExt::validator_stake` / `validator_total_stake`: [1](#0-0) 
which forwards to `self.epoch_info_provider.validator_stake(...)` / `validator_total_stake(...)` and, on any `EpochError`, wraps it as `ExternalError::ValidatorError(e)`.
- `ExternalError` is defined as an "unexpected"/opaque error type, explicitly meant for storage corruption or validator-info lookup failures, not for normal user-triggered failures: [2](#0-1) 
- When the VM returns this as `VMRunnerError::ExternalError`, `execute_function_call` explicitly re-raises it as a `RuntimeError` rather than converting it to a `VMOutcome`/`ActionError`, with a comment stating that most VM errors are translated to user-facing failures but "for all other cases, panicking here is better than leaking the exact details further up": [3](#0-2) 
- At the top of the call stack, `chain/chain/src/runtime/mod.rs` maps `RuntimeError` values from `Runtime::apply` to a chain-level `Error`. Note that most variants there are handled specially (some intentionally left as `// TODO(#2152): process gracefully` `panic!`s), but `RuntimeError::ValidatorError(e)` is converted directly to an opaque chain `Error` (`e.into()`) and returned as a hard failure of the whole `apply_chunk` call, not as a receipt-level `ActionError`: [4](#0-3) 
- One level further out, `apply_chunk` treats any non-`StorageError` `Error` (including the one produced from `ValidatorError`) by simply propagating it (`_ => Err(e)`), i.e. it fails the entire chunk apply for the shard instead of failing only the offending receipt: [5](#0-4) 

This is architecturally the same defect class as the reported Chainlink issue: a call into an external/second system (epoch manager acting in the role of the oracle) that can legitimately fail (e.g., `EpochError::EpochOutOfBounds`, epoch info not present/GC'd for nodes doing state sync/catchup, or resharding-related epoch lookups) is not defensively handled with a try/catch-equivalent (i.e., translated into a graceful `ActionErrorKind`/`Failure` outcome). Instead it is treated as an unrecoverable, chunk-fatal condition, even though it is triggerable by an ordinary, unprivileged contract call.

### Impact Explanation
If any of the epoch-manager lookups invoked from `RuntimeExt::validator_stake`/`validator_total_stake` return `Err(EpochError)` for an epoch_id that a chunk is being applied against — which is plausible for nodes that are catching up, resharding, or otherwise have incomplete/garbage-collected epoch caches relative to the historical or edge-case epoch being replayed — chunk application for that shard fails with a hard `Error` instead of gracefully failing only the one receipt. Because a single, cheaply-craftable contract call (`ext_validator_stake`) can trigger this code path on every node that processes/re-applies that chunk (validators, re-execution during state sync, and other nodes validating the state witness), this can manifest as:
- A transaction-triggered halt/failure of chunk application on any node reaching that faulty code path (nodes catching up, resharding, or replaying historical epochs), rather than a normal `Failure(ActionError)` outcome that would be isolated to the single receipt.
- Divergent behavior between nodes whose epoch-manager caches happen to differ (one node's lookup succeeds, another's transiently fails), which can produce different apply results for the same chunk/state — a state-transition inconsistency risk.

### Likelihood Explanation
The host-function call itself (`ext_validator_stake`) is trivially reachable by any account with a deployed contract and normal gas/transaction fees, as shown by the existing test. The likelihood of actually hitting the failing `EpochError` branch depends on internal epoch-manager cache/GC timing and is not deterministically triggerable from the transaction alone in the steady-state case; it is most likely under catchup/resharding/historical-replay conditions rather than typical mainline block production. This keeps the exploit surface real but conditional, similar in spirit to the "audit-hint" nature of the original Chainlink report (which is also about a low-probability-but-real external dependency failure, not a guaranteed on-demand exploit).

### Recommendation
Treat any `EpochError` returned from `EpochInfoProvider::validator_stake`/`validator_total_stake` (and any other epoch-manager call reachable from a contract-triggered host function) as a normal, receipt-scoped failure rather than an opaque `ExternalError`/`RuntimeError` that aborts the whole chunk apply. Concretely:
- In `RuntimeExt::validator_stake`/`validator_total_stake` (`runtime/runtime/src/ext.rs`), distinguish "genuine storage corruption" `EpochError`s from "epoch info not (yet) available for a syncing/catching-up/resharding node" cases, and surface the latter as a `HostError`/`FunctionCallError` that resolves to `ActionErrorKind`/`Failure(ActionError)` for the specific receipt, instead of escalating to `RuntimeError::ValidatorError`.
- In `chain/chain/src/runtime/mod.rs`'s mapping of `RuntimeError` to `Error`, avoid unconditionally propagating `ValidatorError` as a chunk-fatal error; where the underlying `EpochError` indicates recoverable/transient unavailability, retry or defer instead of failing the whole shard's chunk apply.

### Proof of Concept
1. Deploy a WASM contract exposing an entry point that calls the `validator_stake` (or `validator_total_stake`) host function, exactly as exercised by the existing integration test: [6](#0-5) 
2. Submit a `FunctionCall` transaction invoking `ext_validator_stake` from an unprivileged account (no special permissions required — only gas and a deployed contract).
3. On a node whose `EpochManager` cannot resolve `get_epoch_info(epoch_id)` for the chunk's `epoch_id` (e.g., a node performing catchup/resharding/historical chunk re-application), the call chain `RuntimeExt::validator_stake` → `ExternalError::ValidatorError` → `VMRunnerError::ExternalError` → `RuntimeError::ValidatorError` (`runtime/runtime/src/function_call.rs:314-321`) → `chain/chain/src/runtime/mod.rs:373` → `apply_chunk`'s `_ => Err(e)` fallthrough (`chain/chain/src/runtime/mod.rs:1296-1303`) causes the entire chunk-apply operation for that shard to fail, instead of the transaction simply failing with `Failure(ActionError)` as normal contract-triggered errors do.

Note: I was not able to fully verify, within the available index, the exact conditions under which `EpochManager::get_epoch_info` legitimately returns `Err` for an `epoch_id` that a chunk is actively being applied against (e.g., precise GC/cache-eviction timing during catchup or resharding) — this would require deeper tracing through `chain/epoch-manager/src/lib.rs` and `EpochManagerHandle` caching logic than the index exposes. A Devin session with full repository access would be needed to confirm the precise trigger conditions and construct a concrete reproduction against a running multi-node testnet.

### Citations

**File:** runtime/runtime/src/ext.rs (L51-65)
```rust
/// Error used by `RuntimeExt`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ExternalError {
    /// Unexpected error which is typically related to the node storage corruption.
    /// It's possible the input state is invalid or malicious.
    StorageError(StorageError),
    /// Error when accessing validator information. Happens inside epoch manager.
    ValidatorError(EpochError),
}

impl From<ExternalError> for VMLogicError {
    fn from(err: ExternalError) -> Self {
        VMLogicError::ExternalError(AnyError::new(err))
    }
}
```

**File:** runtime/runtime/src/ext.rs (L329-339)
```rust
    fn validator_stake(&self, account_id: &AccountId) -> ExtResult<Option<Balance>> {
        self.epoch_info_provider
            .validator_stake(&self.epoch_id, account_id)
            .map_err(|e| ExternalError::ValidatorError(e).into())
    }

    fn validator_total_stake(&self) -> ExtResult<Balance> {
        self.epoch_info_provider
            .validator_total_stake(&self.epoch_id)
            .map_err(|e| ExternalError::ValidatorError(e).into())
    }
```

**File:** runtime/runtime/src/function_call.rs (L284-321)
```rust
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
```

**File:** chain/chain/src/runtime/mod.rs (L361-374)
```rust
            .map_err(|e| match e {
                RuntimeError::InvalidTxError(err) => {
                    tracing::warn!(?err, "invalid tx");
                    Error::InvalidTransactions
                }
                // TODO(#2152): process gracefully
                RuntimeError::UnexpectedIntegerOverflow(reason) => {
                    panic!("RuntimeError::UnexpectedIntegerOverflow {reason}")
                }
                RuntimeError::StorageError(e) => Error::StorageError(e),
                // TODO(#2152): process gracefully
                RuntimeError::ReceiptValidationError(e) => panic!("{}", e),
                RuntimeError::ValidatorError(e) => e.into(),
            })?;
```

**File:** chain/chain/src/runtime/mod.rs (L1286-1304)
```rust
        match self.process_state_update(
            trie,
            apply_reason,
            chunk,
            block,
            receipts,
            transactions,
            storage_config.state_patch,
        ) {
            Ok(result) => Ok(result),
            Err(e) => match e {
                Error::StorageError(err) => match &err {
                    StorageError::FlatStorageBlockNotSupported(_)
                    | StorageError::MissingTrieValue(..) => Err(err.into()),
                    _ => panic!("{err}"),
                },
                _ => Err(e),
            },
        }
```

**File:** integration-tests/src/tests/client/process_blocks.rs (L3364-3401)
```rust
#[test]
fn test_validator_stake_host_function() {
    init_test_logger();
    let epoch_length = 5;
    let mut genesis = Genesis::test(vec!["test0".parse().unwrap(), "test1".parse().unwrap()], 1);
    genesis.config.epoch_length = epoch_length;
    genesis.config.transaction_validity_period = epoch_length * 2;
    let mut env = TestEnv::builder(&genesis.config).nightshade_runtimes(&genesis).build();
    let genesis_block = env.clients[0].chain.get_block_by_height(0).unwrap();
    let block_height = deploy_test_contract(
        &mut env,
        "test0".parse().unwrap(),
        near_test_contracts::rs_contract(),
        epoch_length,
        1,
    );
    let signer = InMemorySigner::test_signer(&"test0".parse().unwrap());
    let signed_transaction = SignedTransaction::from_actions(
        10,
        "test0".parse().unwrap(),
        "test0".parse().unwrap(),
        &signer,
        vec![Action::FunctionCall(Box::new(FunctionCallAction {
            method_name: "ext_validator_stake".to_string(),
            args: b"test0".to_vec(),
            gas: Gas::from_teragas(100),
            deposit: Balance::ZERO,
        }))],
        *genesis_block.hash(),
    );
    assert_eq!(
        env.rpc_handlers[0].process_tx(signed_transaction, false, false),
        ProcessTxResponse::ValidTx
    );
    for i in 0..3 {
        env.produce_block(0, block_height + i);
    }
}
```
