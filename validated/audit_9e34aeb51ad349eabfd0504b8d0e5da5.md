[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L874-888)
```rust
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
```

**File:** aptos-move/aptos-vm/src/aptos_vm.rs (L1250-1264)
```rust
        let storage_refund = self.charge_change_set(
            &mut user_session_change_set,
            gas_meter,
            txn_data,
            resolver,
            module_storage,
        )?;

        Ok(EpilogueSession::on_user_session_success(
            self,
            txn_data,
            resolver,
            user_session_change_set,
            storage_refund,
        ))
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/user_transaction_sessions/epilogue.rs (L27-31)
```rust
use derive_more::{Deref, DerefMut};
use move_core_types::vm_status::VMStatus;

#[derive(Deref, DerefMut)]
pub struct EpilogueSession<'r> {
```
