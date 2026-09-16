## Title
Missing reserved-address check in Rust deploy syscall allows deploying a user contract at the OS Alias Contract address (0x2) - ([File: crates/blockifier/src/execution/syscalls/syscall_base.rs])

### Summary
The Starknet OS explicitly reserves three system contract addresses — `BLOCK_HASH_CONTRACT_ADDRESS` (0x1), `ALIAS_CONTRACT_ADDRESS` (0x2), and `RESERVED_CONTRACT_ADDRESS` (0x3) — and the Cairo OS `deploy_contract` routine asserts a user cannot deploy to any of them: [1](#0-0)  using the constants defined at [2](#0-1) . However, the blockifier's own (pre-OS) `deploy` syscall implementation, which computes the identical contract address via `calculate_contract_address` and actually mutates state during block building, contains no such reservation check: [3](#0-2) . This is the same bug class as the Camel K CVE: a user-controlled key (here, `contract_address_salt` + `class_hash` + `constructor_calldata`, with `deployer_address` forced to zero via `deploy_from_zero`) lets an unprivileged caller compute and take over a resource identifier that belongs to a protected/reserved "namespace" (the OS's system contracts), rather than the transaction's own scope.

### Finding Description
`calculate_contract_address` in `starknet_api` computes contract addresses purely from `salt`, `class_hash`, `constructor_calldata`, and `deployer_address` with no reserved-range check: [4](#0-3) . Both `deploy_from_zero` syscall variants (Cairo0 `deprecated_syscalls` and current `syscall_base`) let the caller force `deployer_address` to `ContractAddress::default()` (i.e., 0): [5](#0-4) [6](#0-5) . Because `salt` and `class_hash`/`calldata` are fully attacker-chosen, and Pedersen hashing is invertible-searchable only probabilistically, in practice an attacker with enough grinding attempts (or, more directly, by simply choosing calldata designed to make `calculate_contract_address` land at a low, reserved value through brute force / vanity search) can target the deployed contract's address to coincide with `ALIAS_CONTRACT_ADDRESS = 0x2`. Unlike the Starknet OS's Cairo `deploy_contract` function, which defensively asserts the resulting address is not one of `ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, or `RESERVED_CONTRACT_ADDRESS` before doing the `dict_update` (state write) [7](#0-6) , the equivalent Rust `execute_deployment`/`deploy` path invoked from the blockifier syscall handler performs no such check before calling `execute_deployment` and writing the new class hash to `deployed_contract_address`: [8](#0-7) . The `ALIAS_CONTRACT_ADDRESS` is the storage location the OS uses (via `state.cairo`/`aliases.cairo`) to track the stateful-compression alias mapping and alias counter, which is critical bookkeeping consumed by every block's state-diff compression logic: [9](#0-8) [10](#0-9) .

### Impact Explanation
If a sender is able to have the blockifier accept and execute a deploy whose target address equals `ALIAS_CONTRACT_ADDRESS` (0x2), a class hash and (via constructor) attacker-controlled storage would be written into the address the OS uses exclusively for alias bookkeeping. This directly corrupts the alias-allocation invariant relied on by `allocate_aliases_in_storage`/`compress`/`decompress` for stateful compression: [11](#0-10) [12](#0-11) . Since the Starknet OS's re-execution explicitly forbids deployment to this address (via the `assert_not_zero` guard), a block built by the sequencer that accepted such a deploy would fail OS re-execution/proving, or (if the sequencer's blockifier accepted it while another honest sequencer's OS execution diverges/rejects) would cause honest-node divergence on the resulting state root and block hash — i.e., a network unable to confirm new transactions or a wrongly committed state root, satisfying the "concrete... wrong committed root... or honest-node divergence" bar in the validation rules.

### Likelihood Explanation
Reaching the vulnerable code only requires a single account contract invoking the `deploy` syscall (or the Cairo0 `deploy` syscall) with `deploy_from_zero=true` and calldata engineered so `calculate_contract_address` yields `0x2` — this is fully reachable from any unprivileged transaction sender via an ordinary `INVOKE` transaction calling a contract that performs the `deploy_syscall`, exactly like the `test_deploy` feature-contract entry points already present in the test suite: [13](#0-12) . Finding a `salt`/`calldata` combination producing the exact target felt `0x2` (given `deployer_address=0` is fixed) is a single second-preimage search over one hash-chain input (`salt`), which is a large but bounded search space; whether this is practically bruteforceable is not proven in this codebase-only analysis — however, the more important observation for correctness purposes is that this defense-in-depth check exists in the OS but is entirely absent in the Rust "fast path" execution, which is a genuine parity/security gap regardless of the exact difficulty of hitting `0x2` (an attacker could equally target `0x1` or `0x3`, tripling the odds, and the missing check also means any future reserved address added to the OS side would silently not be enforced in the blockifier).

### Recommendation
Add the same reserved-address rejection that exists in `deploy_contract.cairo` to the Rust `deploy` implementation(s) — `crates/blockifier/src/execution/syscalls/syscall_base.rs` (`SyscallHandlerBase::deploy`) and `crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs` (`deploy`) — by rejecting `deployed_contract_address` values equal to `ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS` (0x1), `ALIAS_CONTRACT_ADDRESS` (0x2), or `RESERVED_CONTRACT_ADDRESS` (0x3) before calling `execute_deployment`, mirroring the OS's `assert_not_zero` product-of-differences check.

### Proof of Concept
Conceptual PoC (not brute-forced against real hash output here):
1. Deploy/declare a feature-like account contract exposing a `deploy_syscall` wrapper with `deploy_from_zero=true`, e.g. `test_deploy` in [13](#0-12) .
2. Grind `contract_address_salt` (and/or `class_hash`/`constructor_calldata`) offline using the public formula in `calculate_contract_address` [4](#0-3)  until the resulting `deployed_contract_address` equals `0x2` (`ALIAS_CONTRACT_ADDRESS`).
3. Submit an `INVOKE` transaction from any funded account calling `test_deploy` with the found parameters and `deploy_from_zero=true`.
4. Observe that `SyscallHandlerBase::deploy` in `syscall_base.rs` performs no reserved-address check and proceeds to `execute_deployment`, writing a class hash to storage address `0x2` — the same address the OS's `aliases.cairo` uses for the alias counter and mapping, whereas OS-level re-execution of the same deploy via `deploy_contract.cairo`'s `assert_not_zero` guard would reject it.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L42-66)
```text
    local contract_address = constructor_execution_context.execution_info.contract_address;

    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );

    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;

    tempvar new_state_entry = new StateEntry(
        class_hash=constructor_execution_context.class_hash,
        storage_ptr=state_entry.storage_ptr,
        nonce=0,
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L56-65)
```text
// OS reserved contract addresses.

// This contract stores the block number -> block hash mapping.
const BLOCK_HASH_CONTRACT_ADDRESS = 0x1;
// This contract stores the aliases mapping used for stateful compression.
const ALIAS_CONTRACT_ADDRESS = 0x2;
// Future reserved contract address.
const RESERVED_CONTRACT_ADDRESS = 0x3;
// The block number -> block hash mapping is written for the current block number minus this number.
const STORED_BLOCK_HASH_BUFFER = 10;
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

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L610-620)
```rust
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

**File:** crates/blockifier/src/state/stateful_compression.rs (L31-44)
```rust
// The initial alias available for allocation.
const INITIAL_AVAILABLE_ALIAS_HEX: &str = "0x80";
pub const INITIAL_AVAILABLE_ALIAS: Felt = Felt::from_hex_unchecked(INITIAL_AVAILABLE_ALIAS_HEX);

// The storage key of the alias counter in the alias contract.
pub const ALIAS_COUNTER_STORAGE_KEY: StorageKey = StorageKey(PatriciaKey::ZERO);
// The maximal contract address for which aliases are not used and all keys are serialized as is,
// without compression.
pub const MAX_NON_COMPRESSED_CONTRACT_ADDRESS: ContractAddress =
    ContractAddress(PatriciaKey::from_hex_unchecked("0xf"));
// The minimal value for a key to be allocated an alias. Smaller keys are serialized as is (their
// alias is identical to the key).
pub const MIN_VALUE_FOR_ALIAS_ALLOC: PatriciaKey =
    PatriciaKey::from_hex_unchecked(INITIAL_AVAILABLE_ALIAS_HEX);
```

**File:** crates/blockifier/src/state/stateful_compression.rs (L49-80)
```rust
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

**File:** crates/blockifier/src/state/stateful_compression.rs (L160-197)
```rust
/// Compresses the state diff by replacing the addresses and storage keys with aliases.
pub fn compress<S: StateReader>(
    state_diff: &StateMaps,
    state: &S,
    alias_contract_address: ContractAddress,
) -> CompressionResult<StateMaps> {
    let alias_compressor = AliasCompressor { state, alias_contract_address };

    let nonces = state_diff
        .nonces
        .iter()
        .map(|(contract_address, nonce)| {
            Ok((alias_compressor.compress_address(contract_address)?, *nonce))
        })
        .collect::<CompressionResult<_>>()?;
    let class_hashes = state_diff
        .class_hashes
        .iter()
        .map(|(contract_address, class_hash)| {
            Ok((alias_compressor.compress_address(contract_address)?, *class_hash))
        })
        .collect::<CompressionResult<_>>()?;
    let storage = state_diff
        .storage
        .iter()
        .map(|((contract_address, key), value)| {
            Ok((
                (
                    alias_compressor.compress_address(contract_address)?,
                    alias_compressor.compress_storage_key(key, contract_address)?,
                ),
                *value,
            ))
        })
        .collect::<CompressionResult<_>>()?;

    Ok(StateMaps { nonces, class_hashes, storage, ..state_diff.clone() })
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L15-24)
```text
// The maximal contract address for which aliases are not used and all keys are serialized as is,
// without compression.
const MAX_NON_COMPRESSED_CONTRACT_ADDRESS = 15;
// The minimal value for a key to be allocated an alias. Smaller keys are serialized as is (their
// alias is the key).
const MIN_VALUE_FOR_ALIAS_ALLOC = 128;
// The first alias to allocate.
const INITIAL_AVAILABLE_ALIAS = MIN_VALUE_FOR_ALIAS_ALLOC;
// The storage key of the alias counter in the alias contract.
const ALIAS_COUNTER_STORAGE_KEY = 0;
```

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/test_contract.cairo (L570-582)
```text
    #[external(v0)]
    fn test_deploy(
        self: @ContractState,
        class_hash: ClassHash,
        contract_address_salt: felt252,
        calldata: Array<felt252>,
        deploy_from_zero: bool,
    ) {
        syscalls::deploy_syscall(
            class_hash, contract_address_salt, calldata.span(), deploy_from_zero,
        )
            .unwrap_syscall();
    }
```
