[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L860-889)
```rust
        let mut epilogue_session = EpilogueSession::on_user_session_failure(
            self,
            txn_data,
            resolver,
            previous_session_change_set,
        );

        // Abort information is injected using the user defined error in the Move contract.
        let status = self.inject_abort_info_if_available(
            module_storage,
            traversal_context,
            log_context,
            status,
        );
        epilogue_session.execute(|session| {
            transaction_validation::run_failure_epilogue(
                session,
                module_storage,
                serialized_signers,
                gas_meter.balance(),
                fee_statement,
                self.features(),
                txn_data,
                log_context,
                traversal_context,
                self.is_simulation,
            )
        })?;
        epilogue_session.finish(fee_statement, status, change_set_configs, module_storage)
    }
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/user_transaction_sessions/session_change_sets.rs (L53-62)
```rust
    fn write_op_info_iter_mut<'a>(
        &'a mut self,
        executor_view: &'a dyn ExecutorView,
        module_storage: &'a impl AptosModuleStorage,
        fix_prev_materialized_size: bool,
    ) -> impl Iterator<Item = PartialVMResult<WriteOpInfo<'a>>> {
        self.change_set
            .write_op_info_iter_mut(executor_view, module_storage, fix_prev_materialized_size)
            .chain(self.module_write_set.write_op_info_iter_mut(module_storage))
    }
```

**File:** aptos-move/aptos-vm-types/src/abstract_write_op.rs (L71-95)
```rust
    pub fn materialized_size(&self) -> WriteOpSize {
        use AbstractResourceWriteOp::*;
        match self {
            Write(write, _) => write.write_op_size(),
            WriteWithDelayedFields(WriteWithDelayedFieldsOp {
                write_op,
                materialized_size,
                ..
            }) => write_op.project_write_op_size(|| *materialized_size),
            WriteResourceGroup(GroupWrite {
                metadata_op: write_op,
                maybe_group_op_size,
                ..
            }) => write_op.project_write_op_size(|| maybe_group_op_size.map(|x| x.get())),
            InPlaceDelayedFieldChange(InPlaceDelayedFieldChangeOp {
                materialized_size, ..
            })
            | ResourceGroupInPlaceDelayedFieldChange(ResourceGroupInPlaceDelayedFieldChangeOp {
                materialized_size,
                ..
            }) => WriteOpSize::Modification {
                write_len: *materialized_size,
            },
        }
    }
```

**File:** aptos-move/aptos-vm-types/src/abstract_write_op.rs (L97-136)
```rust
    pub fn prev_materialized_size(
        &self,
        state_key: &StateKey,
        executor_view: &dyn ExecutorView,
        fix_prev_materialized_size: bool,
    ) -> PartialVMResult<u64> {
        use AbstractResourceWriteOp::*;
        let size = if fix_prev_materialized_size {
            match self {
                Write(..) | WriteWithDelayedFields(_) => {
                    executor_view.get_resource_state_value_size(state_key)?
                },
                InPlaceDelayedFieldChange(InPlaceDelayedFieldChangeOp {
                    materialized_size,
                    ..
                }) => *materialized_size,
                ResourceGroupInPlaceDelayedFieldChange(
                    ResourceGroupInPlaceDelayedFieldChangeOp {
                        materialized_size, ..
                    },
                ) => *materialized_size,
                WriteResourceGroup(GroupWrite {
                    prev_group_size, ..
                }) => *prev_group_size,
            }
        } else {
            match self {
                Write(..)
                | WriteWithDelayedFields(WriteWithDelayedFieldsOp { .. })
                | InPlaceDelayedFieldChange(_)
                | ResourceGroupInPlaceDelayedFieldChange(_) => {
                    executor_view.get_resource_state_value_size(state_key)?
                },
                WriteResourceGroup(GroupWrite {
                    prev_group_size, ..
                }) => *prev_group_size,
            }
        };
        Ok(size)
    }
```

**File:** aptos-move/aptos-gas-meter/src/traits.rs (L185-197)
```rust
        let fix_prev_materialized_size = self.feature_version() > RELEASE_V1_30;
        for res in change_set.write_op_info_iter_mut(
            executor_view,
            module_storage,
            fix_prev_materialized_size,
        ) {
            let ChargeAndRefund { charge, refund } = pricing.charge_refund_write_op(
                params,
                res.map_err(|err| err.finish(Location::Undefined))?,
            );
            write_fee += charge;
            total_refund += refund;
        }
```
