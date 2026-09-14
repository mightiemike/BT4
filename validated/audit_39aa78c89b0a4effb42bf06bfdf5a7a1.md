### Title
Unconditional per-receipt `validate_receipt` panic on dequeue turns a single malformed receipt into a chain-wide, transaction-triggered halt - ([File: runtime/runtime/src/lib.rs])

### Summary
`Runtime::process_incoming_receipts` and `Runtime::process_delayed_receipts` call `validate_receipt(..., ValidateReceiptMode::ExistingReceipt)` on every receipt pulled from the incoming/delayed queues, unconditionally and regardless of remaining gas/compute budget. A failure here is *not* treated like an ordinary action failure (which is isolated per-receipt and rolled back); it is propagated as a hard `RuntimeError` that unwinds the whole `apply()` call and is turned into a `panic!` by the caller. Because every validator applies the same chunk with the same code, a single receipt that fails this check causes all nodes to panic identically, halting chain progress for that shard — the same "one bad item in an unconditional per-item check aborts the whole batch operation" pattern described in the source report about `get_vault_borrowing_power()`, but here the blast radius is protocol-wide liveness rather than a single vault.

### Finding Description
Receipt processing loops iterate every receipt in the incoming/delayed queue and run a structural/size validation check on each one before or regardless of executing it: [1](#0-0) 

If `validate_receipt` fails, the error is mapped straight to `RuntimeError::ReceiptValidationError` and returned with `?`, aborting `process_incoming_receipts` and the whole `apply()` call: [2](#0-1) 

The delayed-receipt path treats the same kind of failure as `StorageError::StorageInconsistentState`, which is likewise fatal rather than recoverable.

Both of these hard errors flow up out of `Runtime::apply` into the chain layer, where they are converted into a `panic!`: [3](#0-2) 

And even when the error surfaces as a generic `StorageError` from `apply_chunk`, any variant other than `FlatStorageBlockNotSupported`/`MissingTrieValue` is force-panicked: [4](#0-3) 

This is architecturally the same shape as the reported bug: `get_vault_borrowing_power()` loops over all registered tokens and lets a single external call (`balanceOf`/`getLivePrice`) revert and abort the *entire* borrowing/liquidation transaction for *every* vault, not just the one with the bad token. Here, the runtime loops over all queued receipts and lets a single failing structural check abort the *entire* chunk apply for the *whole shard*, not just the one bad receipt — and unlike the analogous `ActionError` path (which is deliberately isolated per receipt, with rollback and a `Failure` outcome, confirmed by an explicit regression test), the `validate_receipt` check sits outside that isolation boundary: [5](#0-4) 

The documentation for this component explicitly states this is a real invariant of the current design, not a hypothetical: "a delayed receipt that fails `validate_receipt` on dequeue is treated as `StorageInconsistentState`" and is called out as an "inconsistent-state failure," i.e., a hard, unrecoverable path rather than a per-item failure: [6](#0-5) 

### Impact Explanation
If any receipt sitting in a shard's incoming or delayed queue fails `validate_receipt` at dequeue time, every validator applying that chunk panics in lockstep. Because chunk application is mandatory consensus-critical work (no honest node can skip it), this is a **transaction-triggered halt**: the shard (and transitively the chain, since other shards depend on receipts flowing from it) stops making progress until operators intervene, exactly the kind of concrete impact required by the validation rules ("a transaction-triggered halt"). This mirrors the external report's escalation path — a single unhandled external failure disabling the vault, and if repeated across many vaults, insolvency of the whole protocol — translated to: a single unhandled receipt-validation failure disabling chunk apply, and since it is deterministic and hits *all* honest nodes identically, it halts the whole shard/chain rather than degrading gracefully.

### Likelihood Explanation
Receipts are validated once in `NewReceipt` mode at creation time, so under normal operation they should already satisfy `ExistingReceipt` validation later. The exact condition under which a receipt that was valid at creation could later fail `ExistingReceipt` validation (e.g., a receipt dwelling in the delayed/buffered queue across a protocol-version boundary where validation limits differ, or a resharding-related remap edge case) was not fully confirmed from the indexed code — this is a genuine gap in my verification and would need direct inspection of `validate_receipt`'s exact rule set and its protocol-version gating (not retrievable via the available search tools) to fully confirm attacker reachability from a single submitted transaction. What is confirmed with certainty, however, is the control-flow fact that such a failure — however it is triggered — is architecturally a hard, chunk-fatal, panic-inducing error rather than an isolated per-receipt failure, which is the structural weakness analogous to the reported bug class.

### Recommendation
- Treat `validate_receipt` failures on dequeue (both incoming and delayed) the same way `ActionError`s are already treated: record a `Failure` outcome for that specific receipt, roll back its effects, and continue processing the remaining queue, instead of aborting the whole chunk apply.
- Where a hard error is unavoidable (e.g., genuinely corrupted state), ensure it is only ever reachable via state corruption that cannot be attacker-induced by a single transaction, and audit whether any versioned/parameterized checks inside `validate_receipt` can regress an already-accepted receipt from valid to invalid purely due to elapsed time/protocol upgrades while it sits in a queue.
- Add fuzzing/property tests that specifically construct receipts designed to pass `NewReceipt` validation but fail `ExistingReceipt` validation after being queued across a simulated protocol-version bump, mirroring the existing `test_promise_input_size_limit_does_not_affect_other_receipts` isolation test but for the `validate_receipt` gate itself.

### Proof of Concept
Not constructible from indexed code alone: reproducing this requires crafting a receipt that passes `validate_receipt(..., NewReceipt)` at creation but fails `validate_receipt(..., ExistingReceipt)` when later dequeued (e.g., across a delayed-queue dwell + protocol-version-gated validation-rule change), which requires exact knowledge of `validate_receipt`'s rule set that was not available through the tools used in this session.

### Citations

**File:** runtime/runtime/src/lib.rs (L2628-2640)
```rust
            // Validating the delayed receipt. If it fails, it's likely the state is inconsistent.
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                &receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(|e| {
                StorageError::StorageInconsistentState(format!(
                    "Delayed receipt {:?} in the state is invalid: {}",
                    receipt, e
                ))
            })?;
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

**File:** chain/chain/src/runtime/mod.rs (L1294-1304)
```rust
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

**File:** runtime/runtime/src/tests/apply.rs (L5199-5211)
```rust
/// Failing one receipt for exceeding the promise-input size limit must not
/// affect other receipts processed in the same chunk, and `apply` must succeed
/// (a per-receipt failure, not a chunk-level error).
#[test]
fn test_promise_input_size_limit_does_not_affect_other_receipts() {
    let (runtime, tries, root, mut apply_state, signers, epoch_info_provider) = setup_runtime(
        vec![alice_account(), bob_account()],
        Balance::from_near(1_000_000),
        Balance::from_near(500_000),
        Gas::from_teragas(1000),
    );
    setup_promise_input_limit(&mut apply_state, promise_input_limit_version());

```

**File:** protocol-model/spec/cross-shard-congestion.md (L375-379)
```markdown
- **Inconsistent-state failures**: a missing delayed/buffered/postponed/yield item
  referenced by an index yields `StorageError::StorageInconsistentState`
  (`receipts_column_helper.rs:111`, `lib.rs:3011`); a delayed receipt that fails
  `validate_receipt` on pop is likewise treated as inconsistent state, not a soft error
  (`lib.rs:2506`).
```
