## Analog Found

### Title
`replace_class` syscall is not blocked during `__validate__`, breaking the "validation code must not change" invariant - ([File: crates/blockifier/src/execution/syscalls/syscall_base.rs])

### Summary
The Orchid report's root cause is that a verifier is trusted to be "pure" (its returned decision must not depend on state, and its code must not be swappable after being checked), but nothing actually enforces that invariant, letting a malicious verifier switch its code via `CREATE2`/`SELFDESTRUCT` between the time it is checked and the time it is relied upon. The sequencer has the same class of bug in `SyscallHandlerBase::replace_class`: unlike other state-mutating/state-reading syscalls, it is **not** rejected when `ExecutionMode::Validate` is active, so a contract's `__validate__` entry point can call `replace_class_syscall` and swap its own class hash before `__execute__` runs in the same transaction.

### Finding Description
`SyscallHandlerBase::replace_class` only checks that the target class is declared and is Cairo1, and unconditionally performs `state.set_class_hash_at(...)`: [1](#0-0) 

Compare this to sibling syscalls in the same file that explicitly guard against being called from `__validate__`: [2](#0-1) [3](#0-2) 

`get_class_hash_at`, `meta_tx_v0`, and (conditionally, via `disable_deploy_in_validation_mode`) `deploy` all call `reject_syscall_in_validate_mode`, but `replace_class` has no such call. The existing regression tests for validate-mode restrictions cover `call_contract`, `get_block_hash`, `get_sequencer_address`, `get_block_number`/`get_block_timestamp`, and `deploy`, but never exercise `replace_class` in `ExecutionMode::Validate`: [4](#0-3) 

`AccountTransaction::validate_tx` computes the sender's class hash once for error-reporting purposes and then invokes the `__validate__` entry point: [5](#0-4) 

Because `replace_class` is unguarded, the account's `__validate__` call can rewrite `state.set_class_hash_at(self.call.storage_address, class_hash)` for its own contract address. Since `__execute__` for the same transaction is dispatched afterward by re-resolving the class hash at the sender address, the code that decided "this transaction is valid" is not guaranteed to be the code that actually executes — exactly the "verifier purity" violation from the report, where code checked once is assumed immutable but is not.

### Impact Explanation
This breaks a protocol invariant relied on by the sequencer/mempool/fee model: that `__validate__` characterizes the account contract that will subsequently `__execute__`. An account can advertise (and be declared/checked against) one implementation for the cheap/fast validate path, then swap itself to a different implementation via `replace_class` inside `__validate__`, so that `__execute__` runs under attacker-chosen code that was never subjected to whatever guarantees the original class provided (e.g., an authorization/multisig check baked into a particular class, or fee/resource assumptions tied to a specific class). This is an "unauthorized account action" class impact: the account can effectively execute logic that the validation step never actually vetted, directly analogous to the Orchid verifier being swapped out after being checked "pure."

### Likelihood Explanation
Reachable by any unprivileged transaction sender: simply deploy/declare an account contract whose `__validate__` entry point calls `replace_class_syscall`, then submit an ordinary `invoke`/`deploy_account` transaction from that account. No malicious operator, proposer, or p2p component is required — this is purely a gap in blockifier's own syscall-mode gating (`ExecutionMode::Validate`), enforced consistently everywhere except this one syscall.

### Recommendation
Add a validate-mode guard to `replace_class` symmetric to the other state-mutating syscalls (`get_class_hash_at`, `meta_tx_v0`, `deploy`): reject `replace_class` when `self.context.execution_mode == ExecutionMode::Validate`, i.e. call `self.reject_syscall_in_validate_mode("replace_class")` before performing the class-hash lookup/write in `syscall_base.rs`. Add a regression test mirroring `test_validate_accounts_tx` that exercises a `REPLACE_CLASS` validate scenario and asserts it is rejected with `Unauthorized syscall replace_class in execution mode Validate.`

### Proof of Concept
1. Declare an account contract class `A` whose `__validate__` performs the normal signature checks (or none) and then calls `replace_class_syscall(class_hash_B)`, where `B` is some other declared Cairo-1 class.
2. Deploy an account instance of class `A`.
3. Submit any `invoke`/transaction from this account. During `validate_tx`, `__validate__` (class `A`) executes and calls `replace_class`; because no `ExecutionMode::Validate` check exists in `SyscallHandlerBase::replace_class` (`crates/blockifier/src/execution/syscalls/syscall_base.rs:369-378`), the account's class hash at that storage address is rewritten to `B`.
4. `__execute__` for the same transaction is then dispatched — it resolves the class hash at the sender address again and runs class `B`'s `__execute__` logic instead of `A`'s, even though only `A` was ever "validated."

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L221-233)
```rust
    pub fn get_class_hash_at(
        &mut self,
        contract_address: ContractAddress,
    ) -> SyscallResult<ClassHash> {
        if self.context.execution_mode == ExecutionMode::Validate {
            self.reject_syscall_in_validate_mode("get_class_hash_at")?;
        }

        self.storage_access_tracker.accessed_contract_addresses.insert(contract_address);
        let class_hash = self.state.get_class_hash_at(contract_address)?;
        self.storage_access_tracker.read_class_hash_values.push(class_hash);
        Ok(class_hash)
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L369-378)
```rust
    pub fn replace_class(&mut self, class_hash: ClassHash) -> SyscallResult<()> {
        // Ensure the class is declared (by reading it), and of type V1.
        let compiled_class = self.state.get_compiled_class(class_hash)?;

        if !is_cairo1(&compiled_class) {
            return Err(SyscallExecutionError::ForbiddenClassReplacement { class_hash });
        }
        self.state.set_class_hash_at(self.call.storage_address, class_hash)?;
        Ok(())
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L392-398)
```rust
        let versioned_constants = &self.context.tx_context.block_context.versioned_constants;
        if should_reject_deploy(
            versioned_constants.disable_deploy_in_validation_mode,
            self.context.execution_mode,
        ) {
            self.reject_syscall_in_validate_mode("deploy")?;
        }
```

**File:** crates/blockifier/src/transaction/transactions_test.rs (L2466-2525)
```rust

    // Try to call another contract (forbidden).
    let account_tx = create_account_tx_for_validate_test_nonce_0(FaultyAccountTxCreatorArgs {
        scenario: CALL_CONTRACT,
        additional_data: Some(vec![felt!("0x1991")]), /* Some address different than
                                                       * the address of
                                                       * faulty_account. */
        contract_address_salt: salt_manager.next_salt(),
        resource_bounds: ValidResourceBounds::create_for_testing_no_fee_enforcement(),
        ..default_args
    });
    let error = account_tx.execute(state, block_context).unwrap_err();
    match cairo_version {
        CairoVersion::Cairo0 | CairoVersion::Cairo1(RunnableCairo1::Casm) => {
            check_tx_execution_error_for_custom_hint!(
                error,
                "Unauthorized syscall call_contract in execution mode Validate.",
                validate_constructor,
            );
        }
        #[cfg(feature = "cairo_native")]
        CairoVersion::Cairo1(RunnableCairo1::Native) => {
            check_native_validate_error(
                error,
                "Unauthorized syscall call_contract in execution mode Validate.",
                validate_constructor,
            );
        }
    }

    if let CairoVersion::Cairo1(runnable_cairo1) = cairo_version {
        // Try to use the syscall get_block_hash (forbidden).
        let account_tx = create_account_tx_for_validate_test_nonce_0(FaultyAccountTxCreatorArgs {
            scenario: GET_BLOCK_HASH,
            contract_address_salt: salt_manager.next_salt(),
            additional_data: None,
            resource_bounds: ValidResourceBounds::create_for_testing_no_fee_enforcement(),
            ..default_args
        });
        let error = account_tx.execute(state, block_context).unwrap_err();
        match runnable_cairo1 {
            RunnableCairo1::Casm => {
                check_tx_execution_error_for_custom_hint!(
                    &error,
                    "Unauthorized syscall get_block_hash on recent blocks in execution mode \
                     Validate.",
                    validate_constructor,
                );
            }
            #[cfg(feature = "cairo_native")]
            RunnableCairo1::Native => {
                check_native_validate_error(
                    error,
                    "Unauthorized syscall get_block_hash on recent blocks in execution mode \
                     Validate.",
                    validate_constructor,
                );
            }
        }
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L1016-1039)
```rust
        let storage_address = tx_info.sender_address();
        let class_hash = state.get_class_hash_at(storage_address)?;
        let validate_selector = self.validate_entry_point_selector();
        let validate_call = CallEntryPoint {
            entry_point_type: EntryPointType::External,
            entry_point_selector: validate_selector,
            calldata: self.validate_entrypoint_calldata(),
            class_hash: None,
            code_address: None,
            storage_address,
            caller_address: ContractAddress::default(),
            call_type: CallType::Call,
            initial_gas: *remaining_validation_gas,
        };

        // Note that we allow a revert here and we handle it bellow to get a better error message.
        let validate_call_info = validate_call
            .execute(state, &mut context, remaining_validation_gas)
            .map_err(|error| TransactionExecutionError::ValidateTransactionError {
                error: Box::new(error),
                class_hash,
                storage_address,
                selector: validate_selector,
            })?;
```
