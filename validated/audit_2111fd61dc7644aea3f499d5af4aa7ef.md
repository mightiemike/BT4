## Title
Single receipt validation failure during incoming-receipt processing panics the whole node instead of failing only that receipt - (File: `runtime/runtime/src/lib.rs`, `chain/chain/src/runtime/mod.rs`)

### Summary
The Kintsu `delegate_compound` bug is a "one bad item aborts the whole batch" pattern: a loop over independent agents reverts entirely if a single agent's `compound` call errors, instead of isolating the failure. nearcore's runtime deliberately isolates *most* per-item failures (invalid transactions, individual receipt execution failures) so one bad item doesn't kill the whole chunk. However, one specific step in the same receipt-processing pipeline — re-validation of *incoming* receipts — does **not** follow this isolation pattern: a validation failure on a single incoming receipt propagates as a hard `Err` out of `Runtime::apply`, and the call site converts it into an explicit `panic!`, crashing the entire node process for the whole chunk/shard rather than rejecting only the offending receipt.

### Finding Description
In `Runtime::process_incoming_receipts`, every incoming receipt is unconditionally re-validated with `validate_receipt(...).map_err(RuntimeError::ReceiptValidationError)?` before execution: [1](#0-0) 

Unlike transaction processing (which explicitly isolates invalid transactions — see the doc comment "Invalid transactions ... are skipped" and the `continue` pattern in `process_transactions`) or per-action outcome failures, a failure here is not converted into a failed `ExecutionOutcome`; it is bubbled up with `?` through `process_incoming_receipts` → `process_receipts` → `Runtime::apply`: [2](#0-1) 

At the chain layer, this `RuntimeError` is explicitly turned into a process panic rather than a per-item error, with a `TODO(#2152): process gracefully` acknowledging the gap: [3](#0-2) 

By contrast, delayed receipts hit the same `validate_receipt` call but wrap the failure as `StorageInconsistentState` (still eventually hits the generic panic branch in `apply_chunk`'s error handling), and local/instant receipts skip this re-validation entirely (comment: "we don't need to validate the local receipt, because it's just validated in `verify_and_charge_transaction`"). Only the incoming-receipt path performs this redundant, blocking, fail-fast re-validation of content that was already accepted by the sending shard.

The `ValidateReceiptMode` used for this check (`ExistingReceipt`) is strictly *more permissive* than the `NewReceipt` mode used when receipts are first created — several checks (e.g. `RejectEmptyMethodName`, deploy-action count limit, state-init entry count limit, `WithdrawFromGasKeyNotAllowedInDelegate`) are skipped in `ExistingReceipt` mode specifically so already-in-flight receipts keep executing across protocol upgrades: [4](#0-3) [5](#0-4) [6](#0-5) 

This asymmetry shows the checks that remain shared between `NewReceipt` and `ExistingReceipt` (e.g. `max_actions_per_receipt`, `max_total_prepaid_gas`, `max_length_method_name`, `max_arguments_length`, `max_contract_size`) are exactly the ones capable of firing on the receiving shard for a receipt that validated fine when it was created — for example, if any such limit is lowered by a protocol-config change while the receipt is still in flight (buffered by congestion control / bandwidth scheduling, or sitting in the delayed-receipt queue, which can span many blocks and epoch boundaries). Because a single receipt's `Err` aborts the whole `process_receipts` call for the chunk (not just that receipt), and the chain layer turns that into `panic!`, this single-item failure denies service to the *entire shard's chunk application*, exactly mirroring the reported bug class (one bad element aborting the whole loop) — except here the failure mode is a node crash rather than a benign revert.

### Impact Explanation
A panic in `apply_chunk`/`Runtime::apply` is not a benign transaction failure — it crashes the validator/RPC process attempting to apply the chunk. Because delayed/incoming receipts are deterministic and replayed identically by every node executing the same chunk, once such a receipt exists, every honest node that attempts to apply that chunk hits the same panic, which is a transaction/receipt-triggered chain halt for the affected shard rather than an isolated resource loss for a single account. This satisfies the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Reaching this path requires only that some transaction/receipt chain produce a cross-shard incoming receipt that fails one of the shared (`NewReceipt` + `ExistingReceipt`) validation checks by the time it is delivered — e.g. via a change in `LimitConfig` between when the receipt was created and processed, since congestion control and the delayed-receipt queue can keep receipts in flight across many blocks and epoch/protocol-version boundaries. This is a lower-likelihood, config/timing-dependent trigger (not a directly-crafted single transaction), which limits it to Medium severity, but the code path and the explicit `panic!`/`TODO(#2152)` are concretely present today and not merely theoretical.

### Recommendation
Treat a validation failure on an already-accepted incoming/delayed receipt the same way `process_transactions` treats invalid transactions: record a failed `ExecutionOutcome` for that single receipt (or, if it must not execute, safely re-delay/drop it with a recorded error) instead of propagating a hard `Err`/`panic!` that aborts the entire chunk application. At minimum, resolve the `TODO(#2152)` at `chain/chain/src/runtime/mod.rs:366-372` so `RuntimeError::ReceiptValidationError` (and `UnexpectedIntegerOverflow`) do not panic the process.

### Proof of Concept
1. Observe `runtime/runtime/src/lib.rs:2693-2704`: every incoming receipt must pass `validate_receipt` under `ValidateReceiptMode::ExistingReceipt`, or the whole `process_incoming_receipts` call returns `Err(RuntimeError::ReceiptValidationError)`.
2. This `Err` propagates unhandled through `process_receipts` (`lib.rs:1943-1945`) out of `Runtime::apply`.
3. `chain/chain/src/runtime/mod.rs:361-374` shows the caller explicitly `panic!`s on `RuntimeError::ReceiptValidationError` and `RuntimeError::UnexpectedIntegerOverflow`, both marked `TODO(#2152): process gracefully`.
4. Since `ExistingReceipt` mode still enforces limits like `max_total_prepaid_gas`/`max_actions_per_receipt`/`max_length_method_name` (`runtime/runtime/src/action_validation.rs`), any legitimately-created receipt that becomes non-compliant with those limits by the time it is delivered on the receiving shard (e.g. due to a limit-lowering protocol upgrade while the receipt sits in the congestion-controlled outgoing buffer or delayed-receipt queue) reproduces the panic deterministically on every node applying that chunk — a single receipt causing shard-wide processing failure, analogous to the reported `delegate_compound` DOS but manifesting as a node crash/chain halt.

### Citations

**File:** runtime/runtime/src/lib.rs (L1943-1945)
```rust
        // Step 3: process receipts.
        let process_receipts_result =
            self.process_receipts(&mut processing_state, &mut receipt_sink)?;
```

**File:** runtime/runtime/src/lib.rs (L2693-2704)
```rust
        processing_state.outcomes.reserve(processing_state.incoming_receipts.len());
        for receipt in processing_state.incoming_receipts {
            // Validating new incoming no matter whether we have available gas or not. We don't
            // want to store invalid receipts in state as delayed.
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(RuntimeError::ReceiptValidationError)?;
            if processing_state.total.compute >= compute_limit
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

**File:** runtime/runtime/src/action_validation.rs (L117-123)
```rust
    if mode == ValidateReceiptMode::NewReceipt {
        validate_number_of_deploy_actions(actions, limit_config.max_deploy_actions_per_receipt)?;
        // `DeterministicStateInit` predates the entry limit, so it is applied to newly
        // created receipts only. A receipt built before the limit took effect has to
        // keep executing.
        validate_number_of_state_init_entries(actions, limit_config.max_state_init_entries)?;
    }
```

**File:** runtime/runtime/src/action_validation.rs (L192-198)
```rust
            if mode == ValidateReceiptMode::NewReceipt {
                reject_removed_protocol_feature(
                    ProtocolFeature::RejectDelegateV2,
                    "DelegateV2",
                    current_protocol_version,
                )?;
            }
```

**File:** runtime/runtime/src/action_validation.rs (L321-326)
```rust
    if mode == ValidateReceiptMode::NewReceipt
        && ProtocolFeature::RejectEmptyMethodName.enabled(current_protocol_version)
        && action.method_name.is_empty()
    {
        return Err(ActionsValidationError::FunctionCallEmptyMethodName);
    }
```
