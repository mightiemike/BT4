### Title
Missing reserved-address check in Blockifier's contract deployment (`Deploy` syscall / constructor execution) causes Blockifier–Starknet OS execution divergence - ([File: crates/blockifier/src/execution/execution_utils.rs])

### Summary
The Starknet OS Cairo implementation of contract deployment explicitly forbids deploying a contract to one of several protocol-reserved addresses (`ORIGIN_ADDRESS` = 0, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, `RESERVED_CONTRACT_ADDRESS`). The Rust Blockifier, which is what the sequencer's Gateway/Batcher actually use to execute transactions and build blocks, performs the identical deployment logic (`calculate_contract_address` → `execute_deployment` / `deploy`) but never performs this reserved-address check. A regular user transaction can be crafted (via `deploy_account` or an inner `deploy` syscall from an already-deployed contract) so that the computed deployment address collides with one of these reserved addresses.

### Finding Description
In the Starknet OS Cairo code, `deploy_contract` explicitly asserts the target address is not one of the reserved addresses before writing state: [1](#0-0) 

This check references `ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, and `RESERVED_CONTRACT_ADDRESS`, which are special addresses used by the protocol (address 0 is the "null" caller sentinel; the alias contract address backs the address-aliasing/state-compression mechanism; the block-hash contract address is used by `get_block_hash` queries).

The equivalent Rust execution path in the Blockifier — used for actual block building/execution by the sequencer — computes the address the same way via `calculate_contract_address` and then writes the class hash directly with no equivalent reserved-address guard: [2](#0-1) 

The syscall-level entry point that triggers this (`deploy`), reachable from any contract execution during a normal transaction, also has no such check: [3](#0-2) 

Only the "already deployed" check (`current_class_hash != ClassHash::default()`) exists in the Rust code — it prevents redeploying over an existing contract but does not prevent deploying to a protocol-reserved address in the first place, which is a distinct property enforced only on the OS side.

Because the two execution engines (Blockifier for block building/validation, Starknet OS for re-execution/proving) are supposed to be behaviorally identical, this asymmetry means a transaction that the Blockifier accepts and includes in a block (deploying to, e.g., the alias contract address or block-hash contract address) will be rejected by the Starknet OS during re-execution/proof generation for that same block.

### Impact Explanation
This is a High severity execution-divergence bug reachable from a single unprivileged deploy_account transaction or inner `deploy` syscall call:
- The Blockifier will accept and commit a block containing a deployment to a reserved address (e.g. the alias contract address used for state compression, or the block-hash contract address used for L1↔L2/get_block_hash lookups), corrupting the special-purpose storage that the OS relies on for aliasing/compression and block-hash queries.
- When the Starknet OS re-executes/proves that same block, its `assert_not_zero` guard in `deploy_contract.cairo` will fail, meaning the block cannot be proven — the network becomes unable to confirm/finalize that block, an honest-node divergence between the execution engine used to build the block and the OS used to prove it.
- If the OS check is not hit for some reason (e.g., proving lags or different flows are used) and the corrupted alias/block-hash contract entry persists, subsequent protocol-level address aliasing/state-compression or block-hash lookups can be corrupted, potentially freezing the state read/aliasing mechanism for affected addresses.

### Likelihood Explanation
Likelihood is high: reaching this requires only a normal `deploy_account` transaction or a `deploy` syscall invoked from any already-deployed contract with an attacker-chosen `class_hash`/`contract_address_salt`/`constructor_calldata` combination such that `calculate_contract_address(...)` collides with a reserved address. `calculate_contract_address` is a public, deterministic Pedersen-hash function [4](#0-3) , so an attacker can brute-force a salt to target `ORIGIN_ADDRESS` (0), `ALIAS_CONTRACT_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, or `RESERVED_CONTRACT_ADDRESS`. No special privileges, staking, or proposer role is required — any account can submit the transaction through the Gateway.

### Recommendation
Add the same reserved-address guard used in the Starknet OS (`deploy_contract.cairo`) to the Rust Blockifier's deployment path, specifically in `execute_deployment` (crates/blockifier/src/execution/execution_utils.rs) and/or in `SyscallHandlerBase::deploy` (crates/blockifier/src/execution/syscalls/syscall_base.rs), rejecting deployment when the computed `deployed_contract_address` equals `ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, or `RESERVED_CONTRACT_ADDRESS`, mirroring the OS's `assert_not_zero((addr - ORIGIN_ADDRESS) * (addr - BLOCK_HASH_CONTRACT_ADDRESS) * (addr - ALIAS_CONTRACT_ADDRESS) * (addr - RESERVED_CONTRACT_ADDRESS))` check, so Blockifier and OS behavior stay consistent for every transaction that can trigger a deployment.

### Proof of Concept
1. An attacker computes a `contract_address_salt` and picks any declared `class_hash` and `constructor_calldata` such that `calculate_contract_address(salt, class_hash, constructor_calldata, deployer_address)` equals one of the reserved addresses (e.g., `ALIAS_CONTRACT_ADDRESS`), using the public formula in `crates/starknet_api/src/core.rs` (Pedersen hash mod `L2_ADDRESS_UPPER_BOUND`) — feasible offline by brute-forcing the salt.
2. The attacker submits a `deploy_account` transaction (deployer address implicitly zero, per `crates/starknet_api/src/transaction.rs` `DEPLOYER_ADDRESS = ContractAddress(PatriciaKey::ZERO)`) or calls the `deploy` syscall from within an existing contract, using that salt/class_hash/calldata.
3. The Gateway's stateful validator and the Batcher's Blockifier-based `BlockBuilder` execute the transaction: `execute_deployment` in `crates/blockifier/src/execution/execution_utils.rs` only checks `current_class_hash != ClassHash::default()` (i.e., address not already deployed) — since the reserved address has never been deployed to, this passes, and `state.set_class_hash_at(deployed_contract_address, ctor_context.class_hash)` succeeds, corrupting the reserved slot.
4. The block containing this transaction is committed by the sequencer/consensus.
5. When the Starknet OS re-executes the same block for proving (`deploy_contract` in `crates/apollo_starknet_os_program/.../deploy_contract.cairo`), the `assert_not_zero(...)` guard over `(contract_address - ORIGIN_ADDRESS) * ... * (contract_address - ALIAS_CONTRACT_ADDRESS) * ...` fails because `contract_address` matches one of the reserved values, causing OS execution/proof generation for that block to fail — demonstrating the divergence between the accepted block and the unprovable OS re-execution.

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

**File:** crates/blockifier/src/execution/execution_utils.rs (L325-373)
```rust
/// Instantiates the given class and assigns it an address.
/// Returns the call info of the deployed class' constructor execution.
pub fn execute_deployment(
    state: &mut dyn State,
    context: &mut EntryPointExecutionContext,
    ctor_context: ConstructorContext,
    constructor_calldata: Calldata,
    remaining_gas: &mut u64,
) -> ConstructorEntryPointExecutionResult<CallInfo> {
    let strip_vm_frames = context.versioned_constants().strip_vm_frames_in_sierra_gas;
    // Address allocation in the state is done before calling the constructor, so that it is
    // visible from it.
    let deployed_contract_address = ctor_context.storage_address;
    let current_class_hash =
        state.get_class_hash_at(deployed_contract_address).map_err(|error| {
            ConstructorEntryPointExecutionError::new(
                EntryPointExecutionError::from(error)
                    .annotated(TrackedResource::CairoSteps, strip_vm_frames),
                &ctor_context,
                None,
            )
        })?;
    if current_class_hash != ClassHash::default() {
        return Err(ConstructorEntryPointExecutionError::new(
            EntryPointExecutionError::from(StateError::UnavailableContractAddress(
                deployed_contract_address,
            ))
            .annotated(TrackedResource::CairoSteps, strip_vm_frames),
            &ctor_context,
            None,
        ));
    }

    context.revert_infos.0.push(EntryPointRevertInfo::new(
        deployed_contract_address,
        current_class_hash,
        context.n_emitted_events,
        context.n_sent_messages_to_l1,
    ));
    state.set_class_hash_at(deployed_contract_address, ctor_context.class_hash).map_err(
        |error| {
            ConstructorEntryPointExecutionError::new(
                EntryPointExecutionError::from(error)
                    .annotated(TrackedResource::CairoSteps, strip_vm_frames),
                &ctor_context,
                None,
            )
        },
    )?;
```

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
