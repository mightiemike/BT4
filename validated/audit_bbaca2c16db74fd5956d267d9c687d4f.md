No vulnerability found for this question.

**Reasoning:**

`get_system_transaction_output` at `aptos_vm.rs:258-271` simply calls `session.finish(change_set_configs, module_storage)` on the single session that was passed in, then wraps it into a `VMOutput`. [1](#0-0) 

In `process_dkg_result_inner`, the session (`self.new_session(resolver, session_id, None)`) is created fresh at `dkg.rs:120`, and the only mutating operation performed on it before `get_system_transaction_output` is called is the single `execute_function_bypass_visibility` call for `FINISH_WITH_DKG_RESULT` at `dkg.rs:127-140`. [2](#0-1) 

The earlier `OnChainConfig::fetch_config(resolver)` and `ConfigurationResource::fetch_config(resolver)` calls at `dkg.rs:91-98` read directly through the `resolver` (the `AptosMoveResolver`/`StorageAdapter`), not through the VM `session`. [3](#0-2) 

A Move VM session's `finish()` only captures writes performed by executed Move functions inside that session's data cache — it is not a squash of "resolver reads." Reads made against the resolver prior to session creation do not get folded into the session's change set on `finish()`; only mutations made via `session.execute_function_*` calls populate the change set. Since the session here executed exactly one function (`FINISH_WITH_DKG_RESULT`) and nothing else, `session.finish()` produces a change set reflecting only that function's writes.

This is architecturally identical to how `process_block_epilogue` and block-prologue paths use `get_system_transaction_output` — a single-session, single-call pattern with no squashing of a prior change set — as opposed to `RespawnedSession::finish_with_squashed_change_set`, which is the mechanism actually designed to combine and squash multiple sub-session change sets (prologue/user/epilogue). [4](#0-3) [5](#0-4) 

There is no squash operation invoked in the DKG path at all, and no mechanism by which the earlier `fetch_config` reads could leak into the write set. The resulting `VMOutput.change_set` therefore reflects exactly the writes made by `FINISH_WITH_DKG_RESULT`, and the premise of stale resource writes being carried over does not hold.

### Citations

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L258-271)
```rust
pub(crate) fn get_system_transaction_output(
    session: SessionExt<impl AptosMoveResolver>,
    module_storage: &impl AptosModuleStorage,
    change_set_configs: &ChangeSetConfigs,
) -> Result<VMOutput, VMStatus> {
    let change_set = session.finish(change_set_configs, module_storage)?;

    Ok(VMOutput::new(
        change_set,
        ModuleWriteSet::empty(),
        FeeStatement::zero(),
        TransactionStatus::Keep(ExecutionStatus::Success),
    ))
}
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L2753-2758)
```rust
        let output = get_system_transaction_output(
            session,
            module_storage,
            &self.storage_gas_params(log_context)?.change_set_configs,
        )?;
        Ok((VMStatus::Executed, output))
```

**File:** aptos-move/aptos-vm/src/validator_txns/dkg.rs (L91-98)
```rust
        let dkg_state = OnChainConfig::fetch_config(resolver)
            .ok()
            .flatten()
            .ok_or(Expected(MissingResourceDKGState))?;
        let config_resource = ConfigurationResource::fetch_config(resolver)
            .ok()
            .flatten()
            .ok_or(Expected(MissingResourceConfiguration))?;
```

**File:** aptos-move/aptos-vm/src/validator_txns/dkg.rs (L118-140)
```rust
        // All check passed, invoke VM to publish DKG result on chain.
        let mut gas_meter = UnmeteredGasMeter;
        let mut session = self.new_session(resolver, session_id, None);
        let args = vec![
            MoveValue::Signer(AccountAddress::ONE),
            dkg_node.transcript_bytes.as_move_value(),
        ];

        let traversal_storage = TraversalStorage::new();
        session
            .execute_function_bypass_visibility(
                &RECONFIGURATION_WITH_DKG_MODULE,
                FINISH_WITH_DKG_RESULT,
                vec![],
                serialize_values(&args),
                &mut gas_meter,
                &mut TraversalContext::new(&traversal_storage),
                module_storage,
            )
            .map_err(|e| {
                expect_only_successful_execution(e, FINISH_WITH_DKG_RESULT.as_str(), log_context)
            })
            .map_err(|r| Unexpected(r.unwrap_err()))?;
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/respawned_session.rs (L78-118)
```rust
    pub fn finish_with_squashed_change_set(
        mut self,
        change_set_configs: &ChangeSetConfigs,
        module_storage: &impl ModuleStorage,
        assert_no_additional_creation: bool,
    ) -> Result<VMChangeSet, VMStatus> {
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
    }
```
