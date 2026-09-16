This is the critical finding: `SyscallHandlerBase::deploy` in `crates/blockifier/src/execution/syscalls/syscall_base.rs` computes the deployed contract address via `calculate_contract_address` and then calls `execute_deployment` **without ever checking whether the resulting address collides with the OS-reserved addresses** (`BLOCK_HASH_CONTRACT_ADDRESS = 0x1`, `ALIAS_CONTRACT_ADDRESS = 0x2`, `RESERVED_CONTRACT_ADDRESS = 0x3`) [1](#0-0) . Meanwhile the Cairo OS program's `deploy_contract` explicitly asserts the target address is not equal to any of these reserved addresses before proceeding [2](#0-1) . The reserved addresses are defined as protocol-managed storage that only the OS itself is allowed to write to (block-hash mapping and stateful-compression aliases) [3](#0-2) . [2](#0-1) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L380-426)
```rust
    pub fn deploy(
        &mut self,
        class_hash: ClassHash,
        contract_address_salt: ContractAddressSalt,
        constructor_calldata: Calldata,
        deploy_from_zero: bool,
        remaining_gas: &mut u64,
    ) -> SyscallResult<(ContractAddress, CallInfo)> {
        self.increment_syscall_linear_factor_by(
            &SyscallSelector::Deploy,
            constructor_calldata.0.len(),
        );
        let versioned_constants = &self.context.tx_context.block_context.versioned_constants;
        if should_reject_deploy(
            versioned_constants.disable_deploy_in_validation_mode,
            self.context.execution_mode,
        ) {
            self.reject_syscall_in_validate_mode("deploy")?;
        }

        let deployer_address = self.call.storage_address;
        let deployer_address_for_calculation = match deploy_from_zero {
            true => ContractAddress::default(),
            false => deployer_address,
        };
        let deployed_contract_address = calculate_contract_address(
            contract_address_salt,
            class_hash,
            &constructor_calldata,
            deployer_address_for_calculation,
        )?;

        let ctor_context = ConstructorContext {
            class_hash,
            code_address: Some(deployed_contract_address),
            storage_address: deployed_contract_address,
            caller_address: deployer_address,
        };
        let call_info = execute_deployment(
            self.state,
            self.context,
            ctor_context,
            constructor_calldata,
            remaining_gas,
        )?;
        Ok((deployed_contract_address, call_info))
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L42-49)
```text
    local contract_address = constructor_execution_context.execution_info.contract_address;

    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L56-63)
```text
// OS reserved contract addresses.

// This contract stores the block number -> block hash mapping.
const BLOCK_HASH_CONTRACT_ADDRESS = 0x1;
// This contract stores the aliases mapping used for stateful compression.
const ALIAS_CONTRACT_ADDRESS = 0x2;
// Future reserved contract address.
const RESERVED_CONTRACT_ADDRESS = 0x3;
```

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L573-597)
```rust
    fn storage_write(
        &mut self,
        address_domain: u32,
        address: Felt,
        value: Felt,
        remaining_gas: &mut u64,
    ) -> SyscallResult<()> {
        self.pre_execute_syscall(
            remaining_gas,
            self.gas_costs().syscalls.storage_write.base_syscall_cost(),
            SyscallSelector::StorageWrite,
        )?;

        if address_domain != 0 {
            let address_domain = Felt::from(address_domain);
            let error = SyscallExecutorBaseError::InvalidAddressDomain { address_domain }.into();
            return Err(self.handle_error(remaining_gas, error));
        }

        let key = StorageKey::try_from(address)
            .map_err(|e| self.handle_error(remaining_gas, e.into()))?;
        self.base.storage_write(key, value).map_err(|e| self.handle_error(remaining_gas, e))?;

        Ok(())
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/os_utils.cairo (L46-85)
```text
// Writes the hash of the (current_block_number - buffer) block under its block number in the
// dedicated contract state, where buffer=STORED_BLOCK_HASH_BUFFER.
func write_block_number_to_block_hash_mapping{range_check_ptr, contract_state_changes: DictAccess*}(
    block_context: BlockContext*
) {
    alloc_locals;
    tempvar old_block_number = block_context.block_info_for_execute.block_number -
        STORED_BLOCK_HASH_BUFFER;
    let is_old_block_number_non_negative = is_nn(old_block_number);
    if (is_old_block_number_non_negative == FALSE) {
        // Not enough blocks in the system - nothing to write.
        return ();
    }

    // Fetch the (block number -> block hash) mapping contract state.
    local state_entry: StateEntry*;
    %{ GetBlockHashMapping %}

    // Currently, the block hash mapping is not enforced by the OS.
    // TODO(Yoni, 1/1/2026): output this hash.
    local old_block_hash;
    %{ GetOldBlockNumberAndHash %}

    // Update mapping.
    assert state_entry.class_hash = 0;
    assert state_entry.nonce = 0;
    tempvar storage_ptr = state_entry.storage_ptr;
    assert [storage_ptr] = DictAccess(key=old_block_number, prev_value=0, new_value=old_block_hash);
    let storage_ptr = storage_ptr + DictAccess.SIZE;
    %{ WriteOldBlockToStorage %}

    // Update contract state.
    tempvar new_state_entry = new StateEntry(class_hash=0, storage_ptr=storage_ptr, nonce=0);
    dict_update{dict_ptr=contract_state_changes}(
        key=BLOCK_HASH_CONTRACT_ADDRESS,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L84-108)
```text
// Returns the next available alias.
// Initializes the stateful compression feature if needed.
func get_next_available_alias{aliases_storage_updates: DictAccess*, range_check_ptr}() -> felt {
    alloc_locals;
    local next_available_alias;
    %{ ReadAliasCounter %}
    assert aliases_storage_updates[0] = DictAccess(
        key=ALIAS_COUNTER_STORAGE_KEY,
        prev_value=next_available_alias,
        new_value=next_available_alias,
    );
    let aliases_storage_updates = &aliases_storage_updates[1];

    // First time an alias is created.
    if (next_available_alias == 0) {
        %{ InitializeAliasCounter %}
        assert aliases_storage_updates[0] = DictAccess(
            key=ALIAS_COUNTER_STORAGE_KEY, prev_value=0, new_value=INITIAL_AVAILABLE_ALIAS
        );
        let aliases_storage_updates = &aliases_storage_updates[1];
        return INITIAL_AVAILABLE_ALIAS;
    } else {
        return next_available_alias;
    }
}
```
