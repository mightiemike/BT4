Confirmed: `execute_deployment` (`crates/blockifier/src/execution/execution_utils.rs:327-373`) only checks that the target address is uninitialized (`current_class_hash != ClassHash::default()` → `UnavailableContractAddress`). It performs **no check against the OS reserved-address set** (`BLOCK_HASH_CONTRACT_ADDRESS=1`, `ALIAS_CONTRACT_ADDRESS=2`, `RESERVED_CONTRACT_ADDRESS=3`). Same for the syscall entry points that call it: `syscall_base.rs::deploy` [1](#0-0)  and the deprecated syscall handler [2](#0-1) , and `DeployAccountTransaction::run_execute` [3](#0-2) .

In contrast, the Starknet OS Cairo code that re-executes/proves the block enforces this exact restriction with an explicit `assert_not_zero` product check against all four reserved addresses before allowing any deployment [4](#0-3) , using the constants defined at [5](#0-4) . `ContractAddress::validate()` in `starknet_api` also only excludes addresses `<= 1` (`BLOCK_HASH_TABLE_ADDRESS`), not `0x2`/`0x3` [6](#0-5) , and that validation is not invoked from the deploy execution path anyway.

This is a genuine divergence between blockifier (sequencer execution) and the SNOS Cairo re-execution, structurally analogous to the SQLite `ATTACH DATABASE` bug: a normal, permission-less deployer can — with a crafted `contract_address_salt`/constructor calldata whose Pedersen-hash-derived address happens to equal `0x2` (`ALIAS_CONTRACT_ADDRESS`) — deploy an ordinary contract at that address in blockifier, since blockifier has no reserved-address guard. That contract's storage then lives at the same address the block's finalization logic treats as the dedicated alias-mapping namespace: `allocate_aliases_in_storage`/`compress` unconditionally read and write `(alias_contract_address, ALIAS_COUNTER_STORAGE_KEY)` and per-key alias slots at that same address during `finalize_block` [7](#0-6)  and [8](#0-7) . This merges a user-controlled storage domain with the privileged alias-compression bookkeeping domain — the "attach a second database and query/write across the boundary" pattern from the CVE, except here it is contract storage instead of SQL tables.

### Title
Missing reserved-contract-address check in blockifier deploy path allows collision with the OS alias contract, causing sequencer/SNOS state divergence - (File: crates/blockifier/src/execution/execution_utils.rs)

### Summary
`execute_deployment` in blockifier, used by all deploy paths (`deploy` syscall, deprecated `deploy` syscall, `DeployAccountTransaction`), does not reject deployments to the OS-reserved contract addresses (`0x1` block-hash table, `0x2` alias contract, `0x3` reserved). The Starknet OS Cairo program does enforce this restriction in `deploy_contract`. An attacker able to grind a salt/calldata combination whose Pedersen-derived address equals `0x2` can deploy a contract there, which the sequencer will accept and commit into a block, but which will corrupt or conflict with the stateful-compression alias bookkeeping and will fail SNOS's `assert_not_zero` check on re-execution.

### Finding Description
The deployment address availability check in `execute_deployment` only verifies the address is uninitialized, not that it avoids reserved OS addresses [9](#0-8) . All deploy entry points (regular `deploy` syscall, deprecated syscall, and `deploy_account`) route through this function without adding the missing check [10](#0-9) [3](#0-2) .

Meanwhile, the sequencer's block finalization step treats a fixed, hardcoded address (`os_contract_addresses.alias_contract_address()`, default `0x2`) as the exclusive namespace for alias-compression bookkeeping, reading/writing `ALIAS_COUNTER_STORAGE_KEY` and per-key alias slots there for every block when `enable_stateful_compression` is on [11](#0-10) . If any transaction manages to deploy a real user contract to `0x2` before this runs, the alias updater will read/write into that user contract's storage slots, and the user's constructor/subsequent calls can likewise read/write the same slots the alias mechanism relies on (e.g., slot `0`, the alias counter) — since both are literally the same `(contract_address, storage_key)` pairs in the Merkle-committed state.

Separately (and independently sufficient to prove a bug), the Cairo OS program that the sequencer's block must be provable against explicitly forbids this exact deployment via `assert_not_zero` in `deploy_contract.cairo` [12](#0-11) . Since blockifier lacks the equivalent guard, a sequencer can build and commit a block containing such a deployment that the OS cannot re-execute/prove, producing an honest-node divergence between the sequencer's committed state root and what the Starknet OS computes/accepts.

### Impact Explanation
- Honest-node/prover divergence: a block accepted and committed by the sequencer (via blockifier) can be unprovable by the Starknet OS, since the SNOS Cairo program aborts on deployment to a reserved address that blockifier silently allowed. This can stall proving and block finalization for the network.
- Storage/state corruption of the alias-compression mechanism: mixing a user-controlled contract's storage with the alias contract's dedicated bookkeeping namespace can corrupt the compressed state diff computation (`compress`/`decompress` in `crates/blockifier/src/state/stateful_compression.rs`), leading to an incorrect committed state diff/root relative to what full nodes reconstruct via decompression, i.e., wrong committed root.
- This is reachable by an ordinary, permission-less contract deployer (regular `deploy` syscall or `deploy_account` transaction) — no operator/proposer/staker privilege required — satisfying the "single submitted transaction/contract deployer" reachability requirement.

### Likelihood Explanation
Reaching address `0x2` exactly requires finding `salt`/`class_hash`/`constructor_calldata`/`deployer_address` such that the Pedersen-hash-based `calculate_contract_address` result modulo `L2_ADDRESS_UPPER_BOUND` equals `2` [13](#0-12) . This is a preimage search over a ~2^251 domain constrained to hit one of only a few small target values, which is computationally infeasible via brute force with current techniques (not a "grind a few bits" difficulty). This significantly lowers real-world likelihood despite the missing check being a genuine logic gap versus the OS's explicit design invariant. It should still be fixed to match the OS's explicit safety invariant, since relying on hash-preimage hardness alone for a security boundary is fragile and untested by the codebase's own defense-in-depth pattern (the OS has the check; blockifier should mirror it).

### Recommendation
Add an explicit reserved-address check to `execute_deployment` (or upstream in the `deploy` syscall/`DeployAccountTransaction` paths) that mirrors the Cairo OS's `assert_not_zero` guard in `deploy_contract.cairo`: reject deployment when the target address equals `0`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, or `RESERVED_CONTRACT_ADDRESS` (fetched from `versioned_constants.os_constants.os_contract_addresses`), returning a `StateError`/`EntryPointExecutionError` consistent with existing deployment failure handling, so that blockifier and the Starknet OS enforce identical invariants.

### Proof of Concept
1. Attacker searches offline for `(salt, class_hash, constructor_calldata, deployer_address)` such that `calculate_contract_address(...) == ContractAddress(0x2)` (theoretically possible, computationally hard).
2. Attacker submits an `invoke` transaction calling the `deploy` syscall (or a `deploy_account` transaction) with those parameters through a normal account contract.
3. Blockifier's `execute_deployment` finds `current_class_hash == ClassHash::default()` at `0x2` (uninitialized) and allows the deployment, since no reserved-address check exists [14](#0-13) .
4. At block finalization, `allocate_aliases_in_storage` reads/writes alias bookkeeping at `(0x2, key)` for the block's other state diffs, now colliding with the attacker's contract's storage [8](#0-7) .
5. When the Starknet OS attempts to re-execute/prove the same block, `deploy_contract`'s `assert_not_zero` check fails on the attacker's deployment transaction, causing OS execution failure/divergence from the sequencer's already-committed block [12](#0-11) .

Note: I was unable to fully verify address-`0x3` (`RESERVED_CONTRACT_ADDRESS`) or `0x1` handling paths beyond what's cited, and could not execute the hash-preimage search to confirm practical feasibility — likelihood assessment above reflects this uncertainty and is the main reason this is not rated Critical despite the clear logic/parity gap between blockifier and the Starknet OS.

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L380-418)
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
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L593-620)
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
```

**File:** crates/blockifier/src/transaction/transactions.rs (L238-261)
```rust
impl<S: State> Executable<S> for DeployAccountTransaction {
    fn run_execute(
        &self,
        state: &mut S,
        context: &mut EntryPointExecutionContext,
        remaining_gas: &mut u64,
    ) -> TransactionExecutionResult<Option<CallInfo>> {
        let class_hash = self.class_hash();
        let constructor_context = ConstructorContext {
            class_hash,
            code_address: None,
            storage_address: self.contract_address(),
            caller_address: ContractAddress::default(),
        };
        let call_info = execute_deployment(
            state,
            context,
            constructor_context,
            self.constructor_calldata(),
            remaining_gas,
        )?;

        Ok(Some(call_info))
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

**File:** crates/starknet_api/src/core.rs (L269-281)
```rust
impl ContractAddress {
    /// Validates the contract address is in the valid range for external access.
    /// The lower bound is above the special saved addresses and the upper bound is congruent with
    /// the storage var address upper bound.
    pub fn validate(&self) -> Result<(), StarknetApiError> {
        let value = self.0.0;
        let l2_address_upper_bound = Felt::from(*L2_ADDRESS_UPPER_BOUND);
        if (value > BLOCK_HASH_TABLE_ADDRESS.0.0) && (value < l2_address_upper_bound) {
            return Ok(());
        }

        Err(StarknetApiError::OutOfRange { string: format!("[0x2, {l2_address_upper_bound})") })
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

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L246-276)
```rust
    let alias_contract_address = block_context
        .versioned_constants
        .os_constants
        .os_contract_addresses
        .alias_contract_address();
    if block_context.versioned_constants.enable_stateful_compression {
        allocate_aliases_in_storage(block_state, alias_contract_address)?;
    }

    let mut bouncer = bouncer;
    let class_hashes_to_migrate = mem::take(bouncer.get_mut_class_hashes_to_migrate());
    #[cfg(any(test, feature = "testing"))]
    if !class_hashes_to_migrate.is_empty() {
        log::info!(
            "Class hashes to migrate (key = class_hash, value = (compiled_class_hash_v2, \
             compiled_class_hash_v1)): {class_hashes_to_migrate:#?}"
        );
    }

    if !block_context.versioned_constants.enable_casm_hash_migration {
        assert!(
            class_hashes_to_migrate.is_empty(),
            "Class hashes to migrate should be empty when migration is disabled"
        );
    }
    block_state.set_compiled_class_hash_migration(&class_hashes_to_migrate)?;

    let state_diff = block_state.to_state_diff()?.state_maps;

    let compressed_state_diff = if block_context.versioned_constants.enable_stateful_compression {
        Some(compress(&state_diff, block_state, alias_contract_address)?.into())
```

**File:** crates/blockifier/src/state/stateful_compression.rs (L46-80)
```rust
/// Allocates aliases for the new addresses and storage keys in the alias contract.
/// Iterates over the addresses in ascending order. For each address, sets an alias for the new
/// storage keys (in ascending order) and for the address itself.
pub fn allocate_aliases_in_storage<S: StateReader>(
    state: &mut CachedState<S>,
    alias_contract_address: ContractAddress,
) -> StateResult<()> {
    let state_diff = state.to_state_diff()?.state_maps;

    // Collect the contract addresses and the storage keys that need aliases.
    let contract_addresses: BTreeSet<ContractAddress> =
        state_diff.get_contract_addresses().into_iter().collect();
    let mut contract_address_to_sorted_storage_keys = HashMap::new();
    for (contract_address, storage_key) in state_diff.storage.keys() {
        if contract_address > &MAX_NON_COMPRESSED_CONTRACT_ADDRESS {
            contract_address_to_sorted_storage_keys
                .entry(contract_address)
                .or_insert_with(BTreeSet::new)
                .insert(storage_key);
        }
    }

    // Iterate over the addresses and the storage keys and update the aliases.
    let mut alias_updater = AliasUpdater::new(state, alias_contract_address)?;
    for contract_address in contract_addresses {
        if let Some(storage_keys) = contract_address_to_sorted_storage_keys.get(&contract_address) {
            for key in storage_keys {
                alias_updater.insert_alias(key)?;
            }
        }
        alias_updater.insert_alias(&StorageKey(contract_address.0))?;
    }

    alias_updater.finalize_updates()
}
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
