### Title
Missing reserved-address check in blockifier contract deployment allows deploying to OS-reserved special addresses - ([File: crates/blockifier/src/execution/execution_utils.rs])

### Summary
The Starknet OS (Cairo re-execution program) explicitly forbids deploying a contract to any of several reserved special addresses (`ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, `RESERVED_CONTRACT_ADDRESS`). The Rust `blockifier` — the engine that actually executes transactions and builds blocks in this sequencer — contains no equivalent check anywhere in its deployment path, for either the `deploy` syscall or `DeployAccount` transactions.

### Finding Description
The Cairo OS's `deploy_contract` function enforces:
```
assert_not_zero(
    (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
        contract_address - ALIAS_CONTRACT_ADDRESS
    ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
);
``` [1](#0-0) 

This check runs for both the `deploy` syscall path (`execute_deploy_syscall`) [2](#0-1)  and the `DeployAccount` transaction path in the OS, both of which funnel into `deploy_contract`.

In the Rust blockifier, however, the equivalent logic in `execute_deployment` only checks that the target address does not already hold a class hash — it never checks whether the address is one of the OS's reserved special addresses:
```rust
let current_class_hash =
    state.get_class_hash_at(deployed_contract_address)...
if current_class_hash != ClassHash::default() {
    return Err(...UnavailableContractAddress...);
}
``` [3](#0-2) 

This function is reached both from the `deploy` syscall handler (`SyscallHandlerBase::deploy`, which computes `deployed_contract_address = calculate_contract_address(...)` with no reserved-address exclusion) [4](#0-3)  and from `DeployAccountTransaction::run_execute`, which uses `self.contract_address()` (computed purely via `calculate_contract_address`) as the deployment target with no such exclusion either [5](#0-4) .

`calculate_contract_address` itself (`starknet_api::core`) only ensures the resulting address is reduced modulo `L2_ADDRESS_UPPER_BOUND`; it performs no exclusion of `0x0`/`0x1`/other reserved values [6](#0-5) . Note that `ContractAddress::validate()`, which does reject `sender_address == 0` or `== BLOCK_HASH_TABLE_ADDRESS (0x1)`, is only applied by the gateway's `validate_contract_address` to `Declare`/`Invoke` sender addresses — it is explicitly skipped for `DeployAccount` transactions [7](#0-6) , and it is never applied to addresses produced by the `deploy` syscall at all.

Since an attacker fully controls `contract_address_salt`, `class_hash`, and `constructor_calldata` in both a `DeployAccount` transaction and an `Invoke` transaction calling the `deploy` syscall, they can grind these inputs offline until the Pedersen-hash-derived contract address collides with one of the OS's reserved addresses (e.g. the alias contract address used by state compression, whose special storage semantics are defined in `stateful_compression.rs`) [8](#0-7) .

### Impact Explanation
If the blockifier accepts a deployment to a reserved address that the Starknet OS forbids, the sequencer will commit a block/state transition that the Starknet OS re-execution (used to generate the STARK proof of block validity) will reject with the `assert_not_zero` failure. This produces a concrete divergence between the sequencer's committed state/block and what can be proven, i.e. a wrong committed root, or the network becoming unable to confirm/prove the produced block. If the collision lands on the alias contract's special address specifically, it can additionally corrupt the sequencer's own stateful-compression bookkeeping (the alias counter/table used to compress storage keys in state diffs), further threatening state-commitment correctness for subsequent blocks.

### Likelihood Explanation
Reachable directly from a single unprivileged `DeployAccount` transaction or an `Invoke` transaction invoking `deploy_syscall` — no special privileges required. The address space that must be hit (specific reserved felts among `2^251`-ish possible values, modulo `L2_ADDRESS_UPPER_BOUND`) is astronomically large to brute-force via salt grinding for a single collision, which reduces practical likelihood, but the missing check itself is a definite functional gap relative to the OS's enforced invariant, and any future change that shrinks/relaxes the felt domain (or an as-yet-undiscovered address-grinding shortcut) would make exploitation directly practical.

### Recommendation
Add the same reserved-address exclusion enforced by the Starknet OS (`ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`/`BLOCK_HASH_TABLE_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, `RESERVED_CONTRACT_ADDRESS`) to the blockifier's deployment paths — specifically in `execute_deployment` (`crates/blockifier/src/execution/execution_utils.rs`) and/or in `calculate_contract_address`/`ContractAddress::validate` (`crates/starknet_api/src/core.rs`), and ensure it is applied uniformly to `deploy` syscalls and `DeployAccountTransaction::run_execute`, not just to `Declare`/`Invoke` `sender_address` at the gateway layer.

### Proof of Concept
1. An attacker computes, offline, a `(contract_address_salt, class_hash, constructor_calldata)` tuple such that `calculate_contract_address(salt, class_hash, constructor_calldata, deployer_address)` equals one of the Starknet OS's reserved addresses (e.g. the alias contract address) — using the exact same Pedersen-hash formula implemented in `calculate_contract_address` [6](#0-5) .
2. The attacker submits a `DeployAccount` transaction (or an `Invoke` transaction calling the `deploy` syscall) with these parameters.
3. `apollo_gateway`'s `validate_contract_address` does not check `DeployAccount` addresses at all [7](#0-6) , so the transaction passes gateway validation.
4. The blockifier executes `DeployAccountTransaction::run_execute` → `execute_deployment`, which only rejects the deployment if the address already has a nonzero class hash, and otherwise happily writes the attacker's class hash to the reserved address [9](#0-8) .
5. The block is built and committed by the sequencer, but the Starknet OS re-execution of the same transaction would fail the `assert_not_zero` reserved-address check, producing a divergence between the sequencer's committed block and the provable/OS-validated execution.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L229-257)
```text
func execute_deploy_syscall{
    range_check_ptr,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*, syscall_ptr: Deploy*) {
    alloc_locals;
    local caller_execution_info: ExecutionInfo* = caller_execution_context.execution_info;
    local caller_address = caller_execution_info.contract_address;

    let request = syscall_ptr.request;
    // Verify deploy_from_zero is either 0 (FALSE) or 1 (TRUE).
    assert request.deploy_from_zero * (request.deploy_from_zero - 1) = 0;
    // Set deployer_address to 0 if request.deploy_from_zero is TRUE.
    let deployer_address = (1 - request.deploy_from_zero) * caller_address;

    let selectable_builtins = &builtin_ptrs.selectable;
    let hash_ptr = selectable_builtins.pedersen;
    with hash_ptr {
        let (contract_address) = get_contract_address(
            salt=request.contract_address_salt,
            class_hash=request.class_hash,
            constructor_calldata_size=request.constructor_calldata_size,
            constructor_calldata=request.constructor_calldata,
            deployer_address=deployer_address,
        );
    }
```

**File:** crates/blockifier/src/execution/execution_utils.rs (L337-373)
```rust
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

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L400-418)
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L90-98)
```rust
    fn validate_contract_address(tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        let sender_address = match tx {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => tx.sender_address,
            RpcTransaction::DeployAccount(_) => return Ok(()),
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => tx.sender_address,
        };

        Ok(sender_address.validate()?)
    }
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
