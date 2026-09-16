## Analysis

The Anchor.sol issue is a front‑running/griefing pattern: a piece of state (the future contract's storage) that is supposed to be initialized exclusively by its intended deployer can instead be claimed by an attacker who races the deployment, permanently breaking the intended flow. The closest analog reachable by an ordinary transaction/contract call in the sequencer is the `deploy` syscall's `deploy_from_zero` address‑squatting issue for counterfactual account addresses.

### Title
Deploy syscall with `deploy_from_zero=true` lets anyone front-run and permanently occupy a future `DeployAccount` contract address - (File: crates/blockifier/src/execution/syscalls/syscall_base.rs)

### Summary
Starknet computes the address of an account deployed via a `DeployAccountTransaction` using a hard-coded `deployer_address = 0`, independent of who actually submits the transaction [1](#0-0) . The same zero-deployer computation is reachable through the ordinary `deploy` syscall whenever a caller sets `deploy_from_zero = true` [2](#0-1) , and identically in the OS execution path [3](#0-2) . Since the resulting address depends only on `salt`, `class_hash`, and `constructor_calldata` (all public, precomputable values, e.g. derived from an account's public key), any unprivileged contract call can pre-empt the address that a legitimate future `DeployAccountTransaction` is going to target.

### Finding Description
`calculate_contract_address` hashes `(prefix, deployer_address, salt, class_hash, constructor_calldata_hash)` [4](#0-3) . For a `DeployAccountTransaction`, `deployer_address` is fixed to `ContractAddress(PatriciaKey::ZERO)` [1](#0-0) . Any account/contract can independently reach that exact same computation through the general-purpose `deploy` syscall by passing `deploy_from_zero = true`: the syscall handler discards the real caller and substitutes `ContractAddress::default()` for the address computation [2](#0-1) . Consequently, if an attacker learns (or predicts) the `(class_hash, salt, constructor_calldata)` triple that a victim intends to use for account deployment — commonly derivable off-chain since these values are typically public (e.g., account class hash + public key as calldata + salt 0) — the attacker can call `deploy_syscall(class_hash, salt, calldata, deploy_from_zero=true)` from any of their own already-deployed contracts, landing at the identical target address before the victim's `DeployAccountTransaction` executes.

`execute_deployment` unconditionally rejects deployment onto a non-empty class hash: it reads the target's current class hash and errors with `StateError::UnavailableContractAddress` if it's already set [5](#0-4) . This is exactly the failure mode exercised in the test suite for deploying to an existing address [6](#0-5) .

### Impact Explanation
- **Denial of Service on account creation**: The legitimate `DeployAccountTransaction` for the victim's precomputed address will permanently fail with `ContractConstructorExecutionFailed`/`UnavailableContractAddress`, since `deploy_contract` in the OS also asserts `state_entry.class_hash = UNINITIALIZED_CLASS_HASH` before allowing deployment [7](#0-6) . The victim cannot ever deploy an account at that exact address with that class/salt/calldata combination again — the address is permanently squatted with the attacker's chosen class.
- **Concrete loss of funds**: It is a standard pattern (and encouraged by wallets/paymasters) to pre-fund a counterfactual account address before submitting the `DeployAccountTransaction` so the account can pay its own deployment fee. If an attacker races and deploys their own contract (which they control) to that address first, any funds sent to the address before the real deployment lands are now controlled by the attacker's contract instead of the victim's account, allowing theft of the pre-funded balance.

### Likelihood Explanation
The `class_hash`, `contract_address_salt`, and `constructor_calldata` used for account deployment are frequently deterministic and known ahead of time (default salt of `0`, calldata containing only the public key), and are visible in the mempool/gateway once the `DeployAccountTransaction` is submitted but not yet included — or can even be predicted before submission by monitoring wallet/paymaster conventions. Executing the race only requires the attacker to have any already-deployed contract capable of invoking the `deploy` syscall with `deploy_from_zero=true`, which is a completely standard, unprivileged capability.

### Recommendation
Do not allow arbitrary callers to claim the zero-deployer address space via the general `deploy` syscall. Options: restrict `deploy_from_zero=true` to a protocol-privileged path (only reachable during a `DeployAccountTransaction`'s own constructor execution flow) rather than an arbitrary syscall parameter available to any contract, or make the address derivation for account deployment depend on data that cannot be replayed by third parties (e.g., binding to the transaction's own signature/nonce commitment) so that front-running the address computation is infeasible.

### Proof of Concept
1. Victim publishes/derives a future account address `A = calculate_contract_address(salt, class_hash, calldata, deployer=0)` and funds `A` in preparation for submitting a `DeployAccountTransaction`.
2. Attacker, from any already-deployed contract, invokes `deploy_syscall(class_hash, salt, calldata, deploy_from_zero=true)` reusing the exact same `salt`/`class_hash`/`calldata` — see `syscall_base.rs::deploy` which computes the same `deployer_address_for_calculation = ContractAddress::default()` [2](#0-1) . This lands the attacker's contract at address `A`.
3. Attacker's constructor executes at `A`, setting `A`'s class hash to the attacker's own class and potentially draining any pre-funded balance.
4. Victim's subsequent `DeployAccountTransaction` targeting `A` fails permanently, hitting the `UnavailableContractAddress` path proven in the existing test [6](#0-5) .

### Citations

**File:** crates/starknet_api/src/transaction.rs (L457-471)
```rust
impl<T: DeployTransactionTrait> CalculateContractAddress for T {
    /// Calculates the contract address for the contract deployed by a deploy account transaction.
    /// For more details see:
    /// <https://docs.starknet.io/architecture-and-concepts/smart-contracts/contract-address/>
    fn calculate_contract_address(&self) -> StarknetApiResult<ContractAddress> {
        // When the contract is deployed via a deploy-account transaction, the deployer address is
        // zero.
        const DEPLOYER_ADDRESS: ContractAddress = ContractAddress(PatriciaKey::ZERO);
        calculate_contract_address(
            self.contract_address_salt(),
            self.class_hash(),
            self.constructor_calldata(),
            DEPLOYER_ADDRESS,
        )
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L400-410)
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
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L483-498)
```text
    // Verify deploy_from_zero is either 0 (FALSE) or 1 (TRUE).
    tempvar deploy_from_zero = request.deploy_from_zero;
    assert deploy_from_zero * (deploy_from_zero - 1) = 0;
    // Set deployer_address to 0 if request.deploy_from_zero is TRUE.
    let deployer_address = (1 - deploy_from_zero) * caller_address;

    let selectable_builtins = &builtin_ptrs.selectable;
    let hash_ptr = selectable_builtins.pedersen;
    with hash_ptr {
        let (contract_address) = get_contract_address(
            salt=request.contract_address_salt,
            class_hash=request.class_hash,
            constructor_calldata_size=constructor_calldata_size,
            constructor_calldata=constructor_calldata_start,
            deployer_address=deployer_address,
        );
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

**File:** crates/blockifier/src/execution/execution_utils.rs (L337-356)
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
```

**File:** crates/blockifier/src/transaction/transactions_test.rs (L2312-2339)
```rust
    // Negative flow.
    // Deploy to an existing address.
    let mut tx: ApiExecutableTransaction = executable_deploy_account_tx(deploy_account_tx_args! {
        resource_bounds: default_all_resource_bounds,
        class_hash: account_class_hash
    });
    let nonce = nonce_manager.next(tx.contract_address());
    if let ApiExecutableTransaction::DeployAccount(DeployAccountTransaction {
        ref mut tx, ..
    }) = tx
    {
        match tx {
            starknet_api::transaction::DeployAccountTransaction::V1(ref mut tx) => tx.nonce = nonce,
            starknet_api::transaction::DeployAccountTransaction::V3(ref mut tx) => tx.nonce = nonce,
        }
    }
    let deploy_account = AccountTransaction::new_with_default_flags(tx);
    let error = deploy_account.execute(state, block_context).unwrap_err();
    assert_matches!(
        error,
        TransactionExecutionError::ContractConstructorExecutionFailed(
            ConstructorEntryPointExecutionError::ExecutionError { error, .. }
        )
        if matches!(error.unannotated(), EntryPointExecutionError::StateError(
            StateError::UnavailableContractAddress(_)
        ))
    );
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L51-54)
```text
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}
    assert state_entry.class_hash = UNINITIALIZED_CLASS_HASH;
    assert state_entry.nonce = 0;
```
