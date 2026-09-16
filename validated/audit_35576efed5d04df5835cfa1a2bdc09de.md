This confirms the finding: `calculate_contract_address` in `starknet_api/src/core.rs` (used by the Rust blockifier execution engine, e.g. `crates/blockifier/src/execution/syscalls/syscall_base.rs::deploy`) never checks whether the resulting address is one of the reserved addresses (`ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, `RESERVED_CONTRACT_ADDRESS`), whereas the Cairo Starknet OS program (`deploy_contract.cairo`) explicitly enforces this check.

### Title
Missing reserved-address validation in blockifier's `deploy` syscall causes execution/proving divergence - (File: crates/blockifier/src/execution/syscalls/syscall_base.rs)

### Summary
The Starknet OS Cairo program rejects deployments whose computed `contract_address` collides with one of the protocol-reserved addresses (`ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, `RESERVED_CONTRACT_ADDRESS`), [1](#0-0)  but the equivalent Rust `deploy` implementation used by the blockifier during actual block execution performs no such check before computing and using the address. [2](#0-1) 

### Finding Description
A contract deployer (any account transaction or a contract calling the `deploy` syscall) fully controls `class_hash`, `contract_address_salt`, and `constructor_calldata`, which are hashed together with the caller's address to derive the new contract address via `calculate_contract_address`. [3](#0-2)  Because a deployer can freely choose `salt` and `constructor_calldata`, they can brute-force these inputs (a feasible offline search, unlike, e.g., forging a hash preimage) so that the resulting Pedersen-hash-derived address equals a reserved system address such as `ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, or `RESERVED_CONTRACT_ADDRESS`.

In the Starknet OS (Cairo, used for re-execution / proving), `deploy_contract` explicitly asserts the computed `contract_address` is not equal to any of these reserved values before writing state, and will fail if it is. [4](#0-3)  The corresponding Rust code path in the blockifier (`SyscallHandlerBase::deploy` in `syscall_base.rs`, and the deprecated syscall handler equivalent) has no analogous check — it computes `deployed_contract_address` and immediately proceeds to execute the constructor and commit the new `ContractState` entry, regardless of whether that address collides with a reserved address. [5](#0-4) [6](#0-5) 

This is a divergence between the block-production path (blockifier) and the state-transition-proving path (Starknet OS), both of which are supposed to compute identical state transitions for the same block. If the blockifier accepts and commits a deployment to a reserved address (e.g., `ALIAS_CONTRACT_ADDRESS` or `BLOCK_HASH_CONTRACT_ADDRESS`, both of which are used internally by the OS for special bookkeeping, per the aliasing/state mechanisms in `crates/apollo_starknet_os_program/.../state/aliases.cairo` and `os_utils.cairo`), the sequencer will build and commit a block whose state diff the OS re-execution will reject or compute differently, producing a wrong committed root/block hash relative to what honest re-execution would produce, or causing the OS re-execution (proving) step to fail entirely for an otherwise-accepted block.

### Impact Explanation
If an attacker manages to deploy a contract at one of the reserved special addresses, this corrupts data the protocol depends on for internal state-alias bookkeeping and OS-managed pseudo-contracts (block hash storage, address aliasing), potentially permanently corrupting future state commitments or causing honest-node/prover disagreement about the correct state root for the block — a wrong committed root / halt in ability to prove and finalize blocks, which is a High severity impact (state root divergence, chain halted from progressing past that block until manually patched).

### Likelihood Explanation
Exploitability requires finding constructor calldata/salt values whose Pedersen hash collides with a specific reserved felt value; this is a full-domain hash preimage search over the address's field, which is computationally infeasible with current technology (not a small collision space) using the salt/calldata search alone. This substantially lowers the likelihood versus the original zero-address report, since triggering the divergence requires an essentially-infeasible hash inversion rather than merely passing `0`. Given the practical infeasibility of finding such a preimage, the exploitability is Low despite the structural inconsistency being real and demonstrable code-wise.

### Recommendation
Add the same reserved-address check present in `deploy_contract.cairo` to the Rust `deploy` implementations in `crates/blockifier/src/execution/syscalls/syscall_base.rs` (`SyscallHandlerBase::deploy`) and `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs` (`deploy`), rejecting deployment (returning a syscall error / revert) if `deployed_contract_address` equals `ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, or `RESERVED_CONTRACT_ADDRESS`, matching the OS's `assert_not_zero` product check exactly so both execution paths reject identically.

### Proof of Concept
Not practically demonstrable: constructing calldata/salt whose Pedersen-hash-derived contract address exactly equals a specific reserved felt constant requires inverting a cryptographic hash function, which is computationally infeasible. The divergence is proven structurally by comparing the two code paths cited above (OS enforces the check; blockifier does not), but no feasible transaction sequence currently exists to trigger it in practice.

### Citations

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

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L400-426)
```rust
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

**File:** crates/starknet_api/src/core.rs (L326-346)
```rust
pub fn calculate_contract_address(
    salt: ContractAddressSalt,
    class_hash: ClassHash,
    constructor_calldata: &Calldata,
    deployer_address: ContractAddress,
) -> Result<ContractAddress, StarknetApiError> {
    let constructor_calldata_hash = Pedersen::hash_array(&constructor_calldata.0);
    let contract_address_prefix = format!("0x{}", hex::encode(CONTRACT_ADDRESS_PREFIX));
    let address = Pedersen::hash_array(&[
        Felt::from_hex(contract_address_prefix.as_str()).map_err(|_| {
            StarknetApiError::OutOfRange { string: contract_address_prefix.clone() }
        })?,
        *deployer_address.0.key(),
        salt.0,
        class_hash.0,
        constructor_calldata_hash,
    ]);
    let (_, address) = address.div_rem(&L2_ADDRESS_UPPER_BOUND);

    ContractAddress::try_from(address)
}
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L593-646)
```rust
    fn deploy(
        request: DeployRequest,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
    ) -> DeprecatedSyscallResult<DeployResponse> {
        let versioned_constants =
            &syscall_handler.context.tx_context.block_context.versioned_constants;
        if should_reject_deploy(
            versioned_constants.disable_deploy_in_validation_mode,
            syscall_handler.execution_mode(),
        ) {
            return Err(DeprecatedSyscallExecutionError::InvalidSyscallInExecutionMode {
                syscall_name: "deploy".to_string(),
                execution_mode: syscall_handler.execution_mode(),
            });
        }

        let deployer_address = syscall_handler.storage_address;
        let deployer_address_for_calculation = match request.deploy_from_zero {
            true => ContractAddress::default(),
            false => deployer_address,
        };
        let deployed_contract_address = calculate_contract_address(
            request.contract_address_salt,
            request.class_hash,
            &request.constructor_calldata,
            deployer_address_for_calculation,
        )?;

        // Increment the Deploy syscall's linear cost counter by the number of elements in the
        // constructor calldata.
        let syscall_usage = syscall_handler
            .syscalls_usage
            .get_mut(&DeprecatedSyscallSelector::Deploy)
            .expect("syscalls_usage entry for Deploy must be initialized");
        syscall_usage.linear_factor += request.constructor_calldata.0.len();

        let ctor_context = ConstructorContext {
            class_hash: request.class_hash,
            code_address: Some(deployed_contract_address),
            storage_address: deployed_contract_address,
            caller_address: deployer_address,
        };
        let mut remaining_gas = syscall_handler.context.gas_costs().base.default_initial_gas_cost;
        let call_info = execute_deployment(
            syscall_handler.state,
            syscall_handler.context,
            ctor_context,
            request.constructor_calldata,
            &mut remaining_gas,
        )?;
        syscall_handler.inner_calls.push(call_info);

        Ok(DeployResponse { contract_address: deployed_contract_address })
```
