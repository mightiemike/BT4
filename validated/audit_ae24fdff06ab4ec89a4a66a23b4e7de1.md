### Title
Front-Runnable Deploy-From-Zero Address Squatting Collides With Counterfactual DeployAccount Addresses - (File: `crates/blockifier/src/execution/syscalls/syscall_base.rs`)

### Summary
Starknet's counterfactual-account model computes a `DeployAccountTransaction`'s contract address with a fixed `deployer_address = 0` (`DEPLOYER_ADDRESS`) [1](#0-0) . Any contract on the network can reach the exact same address space by invoking the ordinary `deploy_syscall` with `deploy_from_zero = true`, which forces `deployer_address` to zero in the very same `calculate_contract_address` formula [2](#0-1) . Because the address depends only on `(salt, class_hash, constructor_calldata)` and not on transaction type or true submitter identity, an attacker who learns/predicts a victim's future counterfactual account address can pre-empt it via a single ordinary `INVOKE` transaction, deploying an arbitrary class of the attacker's choosing to that address before the legitimate `DeployAccountTransaction` lands.

### Finding Description
The counterfactual deployment model relies on the assumption that the address derived from `(salt, class_hash, constructor_calldata, deployer=0)` is only reachable via a signed `DeployAccountTransaction`, which is gated by `__validate_deploy__` and requires the correct account key. However, the same address space is also reachable from any already-deployed contract through the generic `deploy_syscall` with `deploy_from_zero=true` [3](#0-2) , and this path requires no signature validation tied to the target address at all — only the calling contract's own `__validate__`/`__execute__` runs.

`execute_deployment` only checks that the target address currently has `ClassHash::default()` before writing the attacker's chosen class hash there: [4](#0-3) 

There is no notion of "reserved for future account deployment" or "pre-funded/counterfactual" protection — first writer wins. Once occupied, a later `DeployAccountTransaction` targeting that same address fails with `StateError::UnavailableContractAddress`, as demonstrated by the existing regression test for deploying to an already-used address: [5](#0-4) [6](#0-5) 

This is structurally the same bug class as the Chatwoot Pre-ATO: two independent code paths (weak/attacker-reachable `deploy_syscall(deploy_from_zero=true)` vs. the strong, key-validated `DeployAccountTransaction`) can both claim the same identity slot (the contract address), and the weaker path is not invalidated or reconciled when the legitimate, cryptographically-authenticated path later attempts to claim the same slot. The attacker's illegitimate class occupies the address permanently; the victim's authenticated deployment is rejected outright, and any value already sent to (or intended for) that "reserved" counterfactual address is now controlled by whatever class/contract the attacker deployed there.

### Impact Explanation
Starknet UX widely relies on counterfactual account addresses: wallets/dApps compute the future account address off-chain from `(class_hash, salt=chosen, calldata=[public_key])` and instruct users to fund that address for gas before the account is actually deployed. Since salts are commonly deterministic (e.g., `0` or derived from the public key) and account class hashes are public well-known values (Argent/Braavos/OZ), an attacker monitoring the network, an indexer, or simply guessing common wallet conventions can precompute a not-yet-deployed victim address and squat it with an arbitrary contract via a single `INVOKE` transaction before the victim's `DeployAccountTransaction` executes. Consequences:
- The victim's legitimate `DeployAccountTransaction` permanently fails (`UnavailableContractAddress`), and the account can never be deployed at that address — a permanent denial of the victim's intended account.
- Funds already sent to the counterfactual address (typical practice: pre-funding for deployment fees) become held by the attacker's arbitrary contract/class rather than the intended, key-controlled account — concrete loss/unauthorized control of funds sent to that address.
- This is reachable by a single unprivileged transaction sender/contract deployer (any account can call `deploy_syscall`), satisfying the "single submitted transaction" reachability bar, and represents unauthorized account/address takeover and potential permanent freezing/loss of funds.

### Likelihood Explanation
Exploitation requires only ordinary Starknet capabilities: deploying/using any contract that exposes a `deploy_syscall` call (e.g. a Universal-Deployer-like helper, or the attacker's own contract with a thin wrapper) and knowledge/prediction of the victim's `(class_hash, salt, constructor_calldata)` tuple. These are commonly predictable or observable (e.g., visible in a pending mempool `DeployAccountTransaction`, or derivable from a published public key with a well-known wallet's default salt convention). No special privilege, timing race beyond ordinary front-running, or protocol-level compromise is needed — a single crafted `INVOKE` transaction executed before the victim's `DeployAccountTransaction` suffices.

### Recommendation
Reserve the `deployer_address = 0` address space exclusively for `DeployAccountTransaction`. Concretely: reject (or otherwise namespace) `deploy_syscall` calls with `deploy_from_zero = true` from ordinary contract execution, or compute `DeployAccountTransaction` addresses using a domain-separated deployer identifier that is unreachable from the generic `deploy_syscall` path, so that the counterfactual account address space can never collide with, or be pre-empted by, addresses reachable through regular contract-to-contract deployment.

### Proof of Concept
1. Victim's wallet computes counterfactual account address `A = calculate_contract_address(salt, account_class_hash, [public_key], deployer=0)` and instructs the user to fund `A` before deploying.
2. Attacker (any deployed contract, e.g. via a thin deployer helper) submits an ordinary `INVOKE` transaction calling `deploy_syscall(account_class_hash, salt, [public_key], deploy_from_zero=true)`. Per `syscall_base.rs::deploy`, `deployer_address_for_calculation` becomes `ContractAddress::default()` (zero) [2](#0-1) , producing the identical address `A`.
3. `execute_deployment` finds `current_class_hash == ClassHash::default()` (still counterfactual) and writes the attacker-controlled class hash to `A`, running its constructor [7](#0-6) .
4. Victim's real `DeployAccountTransaction` targeting `A` is later executed; `execute_deployment`/`deploy_contract` now finds `current_class_hash != ClassHash::default()` and fails with `StateError::UnavailableContractAddress`, matching the existing test pattern for deploy-to-unavailable-address [5](#0-4) . Any funds pre-sent to `A` are now under the attacker's deployed contract/class.

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

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L380-410)
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

**File:** crates/blockifier/src/execution/execution_utils.rs (L337-382)
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

    execute_constructor_entry_point(
        state,
        context,
        ctor_context,
        constructor_calldata,
        remaining_gas,
    )
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

**File:** crates/blockifier/src/state/errors.rs (L26-27)
```rust
    #[error("Deployment failed: contract already deployed at address {:#066x}", ***.0)]
    UnavailableContractAddress(ContractAddress),
```
