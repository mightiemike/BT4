### Title
Front-running an account's counterfactual address via the generic `deploy` syscall permanently blocks `DeployAccountTransaction` and freezes pre-funded balances - (File: `crates/blockifier/src/execution/syscalls/syscall_base.rs`)

### Summary
Starknet's counterfactual account model lets users pre-fund an address computed off-chain (from `class_hash`, `contract_address_salt`, `constructor_calldata`, deployer address) before ever submitting a `DeployAccountTransaction`. Any unprivileged contract can front-run that deployment by invoking the generic `deploy` syscall with `deploy_from_zero=true`, which computes the target address with the *same* formula and the *same* zero deployer address used for real accounts. The attacker supplies the victim's known `class_hash`/`salt`/`calldata` and deploys an arbitrary (attacker-controlled) class to that exact address first. When the real owner later submits the `DeployAccountTransaction`, execution fails permanently because the address is already occupied, and any funds pre-sent to that counterfactual address become unrecoverable — the same "DoS via front-run of deployment" bug class as the Kinto `KintoWalletFactory.deployContract` report.

### Finding Description
Contract addresses are computed by `calculate_contract_address`, which hashes `(prefix, deployer_address, salt, class_hash, constructor_calldata_hash)`: [1](#0-0) 

For the generic `deploy` syscall, when `deploy_from_zero` is true, the deployer address used for address calculation is forced to `ContractAddress::default()` (i.e., zero) — exactly the deployer address convention used for `DeployAccountTransaction`'s self-deployed accounts: [2](#0-1) 

The identical logic exists in the deprecated syscall path and the native syscall handler: [3](#0-2) [4](#0-3) 

`DeployAccountTransaction::run_execute` builds a `ConstructorContext` whose `storage_address` is the pre-computed `contract_address` (using deployer address 0), and calls the shared `execute_deployment` helper: [5](#0-4) 

`execute_deployment` is the single choke point used by both the generic `deploy` syscall and `DeployAccountTransaction`. It checks whether the target address already has a non-default class hash and, if so, fails with `StateError::UnavailableContractAddress`: [6](#0-5) 

Because both paths share the exact same address-derivation formula and the same zero-deployer convention, any user who knows (or can predict/observe) a victim's future account `class_hash`, `contract_address_salt` (commonly derived from the account's public key), and `constructor_calldata` can pre-emptively call `deploy_syscall(class_hash, salt, calldata, deploy_from_zero=true)` from any already-deployed contract they control. This deploys an attacker-chosen class to the exact address the victim's account would have occupied, before the victim's `DeployAccountTransaction` is processed. The negative-path test in the codebase already documents that deploying to an address that is already claimed causes the transaction to fail with `UnavailableContractAddress`: [7](#0-6) 

### Impact Explanation
This is directly analogous to the reported Kinto vulnerability: an unprivileged contract call can "claim" the deterministic address reserved for a legitimate account before the intended `DeployAccountTransaction` executes. Consequences:
- The legitimate owner can never deploy their account at that address; the salt/class_hash/calldata combination is exhausted forever (there is no retry with the same parameters since the address is now permanently occupied by attacker-controlled code).
- Starknet's standard UX pattern funds the counterfactual address before deployment (to pay for the deploy-account transaction itself). Any tokens sent to that address before the front-run is discovered are permanently frozen, since the intended account contract (and thus its owner keys) can never be installed there, and the attacker's arbitrary contract has no user-controlled withdrawal path back to the victim.
- This constitutes concrete permanent freezing of funds and denial of a specific account's ability to ever be created, matching the "permanent freezing of funds" / "unauthorized account action" impact bar.

### Likelihood Explanation
The `contract_address_salt` for an account is very often deterministic and derived from a public key that becomes known once a transfer to the counterfactual address is broadcast or once the wallet/dApp reveals it off-chain (a common integration pattern for onboarding). The `class_hash` is typically a well-known, publicly declared account class. An attacker only needs to observe/guess these three public values and issue a single `invoke` transaction from any already-deployed contract calling `deploy_syscall(..., deploy_from_zero=true)` — no special privileges, no validator/proposer control, and no P2P/network assumptions are required. This is reachable purely via ordinary transaction submission.

### Recommendation
Consider one or more of:
- Reserve a distinct address-derivation domain for `DeployAccountTransaction` (e.g., include a domain separator/tag exclusive to account self-deployment) so that the generic `deploy` syscall with `deploy_from_zero=true` can never collide with a `DeployAccountTransaction`-computed address, even when `class_hash`/`salt`/`calldata` match.
- Alternatively, disallow/restrict the `deploy_from_zero=true` variant of the `deploy` syscall from producing addresses that fall in the same domain as account contract addresses, or require a caller-address-derived salt component in the generic syscall so front-running a specific known (deployer=0) address becomes infeasible.
- Document/enforce that dApps must not rely on pre-funding deterministic addresses before deployment, or provide a rescue mechanism at the protocol level (out of scope for a purely code-level fix but worth flagging to the applications/docs team).

### Proof of Concept
1. Victim's wallet SDK computes a future account address `A = calculate_contract_address(salt=S, class_hash=CH, calldata=CD, deployer_address=0)` and displays it to the user to fund before submitting a `DeployAccountTransaction`.
2. Attacker observes `S`, `CH`, `CD` (public wallet class hash + user's known/derivable salt & public key calldata) from the pending intent, mempool, or off-chain UX flow.
3. Attacker, from any already-deployed contract, submits an `invoke` transaction that calls:
   ```
   deploy_syscall(class_hash=CH, contract_address_salt=S, calldata=CD, deploy_from_zero=true)
   ```
   which resolves to the same address `A` via `syscall_base::deploy` → `calculate_contract_address` with `deployer_address_for_calculation = ContractAddress::default()`. [8](#0-7) 
4. `execute_deployment` sets `class_hash_at(A) = attacker_class` since `A` currently has `ClassHash::default()`. [9](#0-8) 
5. Victim's subsequent `DeployAccountTransaction` targeting the same address `A` now fails during `execute_deployment`'s existence check, raising `StateError::UnavailableContractAddress`. [10](#0-9) 
6. Any funds already sent to `A` are permanently stuck: the victim's account contract can never be installed at `A`, and the attacker's deployed class has no legitimate withdrawal path controlled by the victim.

### Citations

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

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L380-411)
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

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L364-394)
```rust
    fn deploy(
        &mut self,
        class_hash: Felt,
        contract_address_salt: Felt,
        calldata: &[Felt],
        deploy_from_zero: bool,
        remaining_gas: &mut u64,
    ) -> SyscallResult<(Felt, Vec<Felt>)> {
        // The cost of deploying a contract is the base cost plus the linear cost of the calldata
        // len.
        let total_gas_cost =
            self.gas_costs().syscalls.deploy.get_syscall_cost(u64_from_usize(calldata.len()));

        self.pre_execute_syscall(remaining_gas, total_gas_cost, SyscallSelector::Deploy)?;

        let (deployed_contract_address, call_info) = self
            .base
            .deploy(
                ClassHash(class_hash),
                ContractAddressSalt(contract_address_salt),
                Calldata(Arc::new(calldata.to_vec())),
                deploy_from_zero,
                remaining_gas,
            )
            .map_err(|err| self.handle_error(remaining_gas, err))?;

        let constructor_retdata = call_info.execution.retdata.0[..].to_vec();
        self.base.inner_calls.push(call_info);

        Ok((Felt::from(deployed_contract_address), constructor_retdata))
    }
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

**File:** crates/blockifier/src/execution/execution_utils.rs (L327-373)
```rust
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

**File:** crates/blockifier/src/transaction/transactions_test.rs (L2312-2338)
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
```
