### Title
Missing re-creation guard in `AbortHookSession::finish` allows repeated storage-fee refund via stale-metadata write-op corruption - (File: `aptos-move/aptos-vm/src/move_vm_ext/session/user_transaction_sessions/abort_hook.rs`)

### Summary
`RespawnedSession::finish_with_squashed_change_set` contains an explicit invariant check (`assert_no_additional_creation`) that is meant to prevent a storage slot deleted earlier in the same transaction from being silently re-created by a later respawned session, which would otherwise let a `ModifyWithMetadata` write op carry stale fee/refund metadata into the committed write set. `EpilogueSession::finish` passes `true` for this flag, but `AbortHookSession::finish` passes `false`, so a slot re-created by the on-abort framework hook is never rejected.

### Finding Description
`RespawnedSession::finish_with_squashed_change_set` computes `additional_change_set` (the delta produced solely by the freshly spawned session's own execution) and, only when `assert_no_additional_creation` is `true`, rejects it if it contains any `Creation` op: [1](#0-0) 

The comment on this code explicitly documents the threat model: a slot created by the user, deleted (claiming a refund), then re-created by respawned session code, producing a `ModifyWithMetadata` write op with the original (stale) metadata — enabling repeated refund extraction. `EpilogueSession::finish` enforces this by passing `true`: [2](#0-1) 

`AbortHookSession::finish`, which respawns a session to run the `RunOnAbort` framework hook on top of the prologue's change set, passes `false` instead: [3](#0-2) 

The result of `AbortHookSession::finish` (a `SystemSessionChangeSet`) becomes the `previous_session_change_set` fed into `EpilogueSession::on_user_session_failure`, which then spawns another `RespawnedSession`: [4](#0-3) 

Critically, the `assert_no_additional_creation` check in the subsequent `EpilogueSession::finish` call only inspects the **new delta** produced by the epilogue session's own execution — not the base change set it was spawned with (which already embeds whatever the abort-hook session wrote): [5](#0-4) 

Therefore, if the on-abort framework logic (invoked via `SessionId::run_on_abort`) re-creates a storage slot that was deleted earlier in the same prologue/user execution, that re-creation is never checked at any stage — not in `AbortHookSession::finish` (guard disabled), and not in the following `EpilogueSession::finish` (guard only looks at the epilogue's own new delta, and the tainted write is already baked into the base).

### Impact Explanation
If exploitable, this breaks the "no re-creation after respawn" invariant that the codebase itself documents as a safeguard against fabricating `ModifyWithMetadata` write ops with stale metadata. Per the code's own threat model, this class of bug enables an attacker to repeatedly claim storage-fee refunds for the same slot across transactions, directly corrupting the committed write set and the associated storage-fee accounting — a state-integrity impact.

### Likelihood Explanation
Exploitability hinges on whether the `RunOnAbort` framework Move function (`transaction_validation::run_on_abort` or equivalent) can, on the specific execution path taken when a transaction aborts, write to/re-create a resource slot that was deleted by the user's own aborted code (or by the prologue) in the same transaction. I was not able to fully inspect the Move framework implementation of the on-abort hook in this session to confirm which storage slots it can affect and under what conditions, so I cannot conclusively establish that this path is triggerable purely with unprivileged transaction input, only that the Rust-level guard asymmetry exists and appears to leave the same class of bug the epilogue explicitly protects against unguarded on the abort path.

### Recommendation
- Pass `true` (or an equivalent stricter check) for `assert_no_additional_creation` in `AbortHookSession::finish`, matching `EpilogueSession::finish`, unless there is a documented reason the abort-hook path is provably incapable of re-creating deleted slots.
- If the abort-hook path is functionally incapable of touching such slots, add an explicit comment/test documenting why the invariant does not need to be re-checked there, and ensure the guard in `EpilogueSession::finish` also considers changes carried in from `previous_session_change_set`, not just the session's own new delta, so upstream sessions (prologue/abort-hook) cannot smuggle in a re-creation that later stages fail to catch.
- Add a regression test that spawns an `AbortHookSession` on a change set that deletes then recreates the same `StateKey`, and assert that `finish` returns an error rather than silently producing a squashed write set.

### Proof of Concept
Conceptual unit test (mirrors the existing invariant test style used for `RespawnedSession`):
```rust
// Build a prologue_session_change_set that deletes StateKey K.
// Spawn an AbortHookSession on top of it (SessionId::run_on_abort).
// Have the on-abort Move logic recreate K.
// Call AbortHookSession::finish(...) and assert it returns Err(...)
// instead of a squashed VMChangeSet containing a Creation/ModifyWithMetadata op for K.
```
I could not execute or fully trace the Move-level `run_on_abort` framework function in this review to confirm it can be driven, by unprivileged transaction content alone, to write into a slot deleted earlier in the same transaction; this would need to be verified against the actual framework bytecode/logic before treating this as a confirmed, end-to-end exploitable bug rather than a code-level invariant gap.

### Citations

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/respawned_session.rs (L84-104)
```rust
        let additional_change_set = self.with_session_mut(|session| {
            unwrap_or_invariant_violation(
                session.take(),
                "VM session cannot be finished more than once.",
            )?
            .finish(change_set_configs, module_storage)
            .map_err(|e| e.into_vm_status())
        })?;
        if assert_no_additional_creation && additional_change_set.has_creation() {
            // After respawning in the epilogue, there shouldn't be new slots
            // created, otherwise there's a potential vulnerability like this:
            // 1. slot created by the user
            // 2. another user transaction deletes the slot and claims the refund
            // 3. in the epilogue the same slot gets recreated, and the final write set will have
            //    a ModifyWithMetadata carrying the original metadata
            // 4. user keeps doing the same and repeatedly claim refund out of the slot.
            return Err(VMStatus::error(
                StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR,
                err_msg("Unexpected storage allocation after respawning session."),
            ));
        }
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/respawned_session.rs (L105-117)
```rust
        let mut change_set = self.into_heads().executor_view.change_set;
        change_set
            .squash_additional_change_set(
                additional_change_set,
                change_set_configs.strict_delayed_field_squash(),
            )
            .map_err(|_err| {
                VMStatus::error(
                    StatusCode::UNKNOWN_INVARIANT_VIOLATION_ERROR,
                    err_msg("Failed to squash VMChangeSet"),
                )
            })?;
        Ok(change_set)
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/user_transaction_sessions/epilogue.rs (L58-72)
```rust
    pub fn on_user_session_failure(
        vm: &AptosVM,
        txn_meta: &TransactionMetadata,
        resolver: &'r impl AptosMoveResolver,
        previous_session_change_set: SystemSessionChangeSet,
    ) -> Self {
        Self::new(
            vm,
            txn_meta,
            resolver,
            previous_session_change_set.unpack(),
            ModuleWriteSet::empty(),
            0.into(),
        )
    }
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/user_transaction_sessions/epilogue.rs (L115-116)
```rust
        let change_set =
            session.finish_with_squashed_change_set(change_set_configs, module_storage, true)?;
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/user_transaction_sessions/abort_hook.rs (L49-61)
```rust
    pub fn finish(
        self,
        change_set_configs: &ChangeSetConfigs,
        module_storage: &impl AptosModuleStorage,
    ) -> Result<SystemSessionChangeSet, VMStatus> {
        let Self { session } = self;
        let change_set =
            session.finish_with_squashed_change_set(change_set_configs, module_storage, false)?;
        let abort_hook_session_change_set =
            SystemSessionChangeSet::new(change_set, change_set_configs)?;

        Ok(abort_hook_session_change_set)
    }
```
