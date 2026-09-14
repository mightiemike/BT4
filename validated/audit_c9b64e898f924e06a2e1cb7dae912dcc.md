### Title
Malicious meta-transaction sender can craft a `DeterministicStateInit` delegate action that passes creation-time validation but fails receiving-shard receipt validation, panicking `apply()` and halting the chain - ([File: runtime/runtime/src/action_validation.rs])

### Summary
`validate_receipt` is invoked twice with different strictness modes for the same receipt content: once at receipt-creation time as `ValidateReceiptMode::NewReceipt` (the sending shard, right after the action executes) [1](#0-0)  and once again on the receiving shard as `ValidateReceiptMode::ExistingReceipt` when the receipt arrives as an incoming cross-shard receipt [2](#0-1) . If the second check fails, the error is not tolerated — it is bubbled up as `RuntimeError::ReceiptValidationError` and turned into a hard `panic!` in `chain/chain/src/runtime/mod.rs`, which every node applying that chunk will hit identically. This mirrors the Optimism report's bug class exactly: data that is accepted/collected at one stage under one set of rules gets strictly re-parsed/re-validated later, and a mismatch aborts processing instead of being handled gracefully.

### Finding Description
`validate_delegate_action` (used to validate `Action::Delegate` payloads, i.e. NEP-366 meta-transactions) explicitly documents a receiver-id mismatch bug for `DeterministicStateInit` actions nested in delegate actions: [3](#0-2) 
The code picks `inner_receiver` differently depending on whether `ProtocolFeature::FixDelegatedDeterministicStateInit` is enabled for the current protocol version. Pre-fix, the validation uses the *outer* delegate receiver instead of the actual inner receiver id that the resulting receipt will target — i.e., a `DeterministicStateInit` action is validated against the wrong `receiver_id`.

Because `apply_action` validates newly generated receipts with `validate_receipt(..., NewReceipt)` right after execution [1](#0-0) , a receipt whose inner action was checked against the wrong receiver can still pass this creation-time check. That receipt is then serialized, forwarded to the target shard, and — on the target shard, in the *next* chunk — re-validated in `process_incoming_receipts` with `ValidateReceiptMode::ExistingReceipt` [2](#0-1) . If it now fails (e.g. `DeterministicStateInit` id derivation mismatch, or any other `ActionsValidationError`), the error type is `ReceiptValidationError`, which is explicitly *not* handled gracefully: [4](#0-3) 
The `// TODO(#2152): process gracefully` comment next to the `panic!("{}", e)` confirms this is a known, unaddressed defect rather than intentional behavior — receipt validation failures on the *receiving* side are treated as fatal instead of being isolated to the offending receipt's outcome (unlike transaction-level failures, which are converted into `ActionError`/failed outcomes and do not crash the node).

The comment in the source itself ("The bug cannot be abused, if someone crafts a state init that passes validation here, it will fail when it is checked as incoming receipt") explains the intended failure path (incoming-receipt validation) but does not account for the fact that failing incoming-receipt validation is implemented as a `panic!`, not a soft rejection.

### Impact Explanation
Every validator/RPC node that applies the chunk containing the malformed incoming receipt executes the identical `panic!` path, since `apply()` is deterministic and run by all nodes tracking that shard. This is a transaction-triggered chain halt: a single unprivileged account can submit a meta-transaction (`SignedDelegateAction`) containing a nested `DeterministicStateInit` action crafted so that:
1. it validates under the (buggy) outer-receiver check at creation time, but
2. fails the stricter/derivation-specific check when reprocessed as an incoming receipt with the correct/actual receiver id.

This matches the required impact bar of "a transaction-triggered halt" — the whole shard (and consequently block production/finality for that shard) stops making progress until operators patch and restart nodes, exactly analogous to the Optimism migration halt caused by a single malformed message.

### Likelihood Explanation
The trigger requires only:
- Constructing a `SignedDelegateAction` (any account can send meta-transactions),
- Nesting a `DeterministicStateInit` action inside it targeting a specific inner receiver different from the outer delegate receiver,
- Running on a protocol version where `FixDelegatedDeterministicStateInit` is not yet active for the relevant validation path, or hitting any other divergence between `NewReceipt` and `ExistingReceipt` outcomes for the same content.

This is directly reachable by any transaction signer with no special privileges, matching the report's "arbitrary user can halt migration/processing" pattern. The main uncertainty is whether `FixDelegatedDeterministicStateInit` is already active on the current mainnet `PROTOCOL_VERSION` in this snapshot — the version-gating table for this feature could not be fully retrieved within the available tool budget, so it is not confirmed whether this specific instance is presently exploitable in production or already patched. Regardless, the structural weakness remains: `ReceiptValidationError` for any incoming receipt validation failure is architecturally mapped to a hard `panic!` rather than a per-receipt failure, so any future or newly discovered divergence between `NewReceipt` and `ExistingReceipt` validation (of which this delegate/deterministic-state-init case is one documented instance) reproduces the same halt.

### Recommendation
- Do not `panic!` on `RuntimeError::ReceiptValidationError` in `chain/chain/src/runtime/mod.rs`. Convert it into a graceful, per-receipt failure (e.g., emit a failed `ExecutionOutcome` for that receipt, or drop/skip it with a state-consistency note) rather than aborting chunk application entirely, consistent with how `InvalidTxError` is already handled gracefully in the same match arm.
- Close the root-cause validation mismatch: ensure `validate_delegate_action`'s inner-action validation always uses the actual (inner) receiver id, unconditionally, removing protocol-version gating for the correctness of receiver-id resolution, or otherwise guarantee that anything accepted under `NewReceipt` mode is provably always accepted under `ExistingReceipt` mode for the same content.
- Add a fuzz/property test asserting `validate_receipt(receipt, NewReceipt).is_ok() ⟹ validate_receipt(receipt, ExistingReceipt).is_ok()` for all receipt/action shapes, to catch future NewReceipt/ExistingReceipt divergences before they can panic a live shard.

### Proof of Concept
Conceptual PoC (exact reproduction depends on confirming the current protocol version's feature gating, which could not be fully verified):
1. Attacker account signs a `SignedTransaction` containing `Action::Delegate` wrapping a `DelegateAction` whose `receiver_id` is account `B`.
2. Inside the delegate action's inner actions, include a `DeterministicStateInit` action whose derived/target id corresponds to a different account `C` (exploiting the outer-vs-inner receiver mismatch described in `validate_delegate_action`).
3. On the signer's shard, `apply_action` executes the delegate action and validates the newly created receipt with `NewReceipt` mode using the (incorrect, pre-fix) receiver resolution — it passes and the receipt is forwarded.
4. In the following chunk, the target shard (owning inner receiver `C`) processes the receipt as incoming via `process_incoming_receipts`, calling `validate_receipt(..., ExistingReceipt)`.
5. Validation fails because the actual `DeterministicStateInit` semantics for receiver `C` differ from what was checked for `B`, returning `Err(ReceiptValidationError::...)`.
6. `Runtime::apply` propagates `RuntimeError::ReceiptValidationError(e)`; `chain/chain/src/runtime/mod.rs` executes `panic!("{}", e)`, crashing every node applying that shard's chunk.

### Citations

**File:** runtime/runtime/src/lib.rs (L871-871)
```rust
            action_receipt
```

**File:** runtime/runtime/src/lib.rs (L2694-2703)
```rust
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
```

**File:** runtime/runtime/src/action_validation.rs (L249-260)
```rust
    let inner_receiver =
        if ProtocolFeature::FixDelegatedDeterministicStateInit.enabled(current_protocol_version) {
            // This is the correct receiver id to use for the check.
            delegate_action.receiver_id()
        } else {
            // This is a bug fixed with `FixDelegatedDeterministicStateInit` that
            // validated against the wrong id. This makes it impossible to
            // initialize deterministic accounts from meta transactions.
            // The bug cannot be abused, if someone crafts a state init that passes
            // validation here, it will fail when it is checked as incoming receipt.
            receiver
        };
```

**File:** chain/chain/src/runtime/mod.rs (L361-373)
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
```
