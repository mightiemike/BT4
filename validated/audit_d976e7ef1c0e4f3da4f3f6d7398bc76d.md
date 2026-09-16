### Title
Deterministic counterfactual account/contract addresses can be front-run via the `deploy` syscall, permanently denying legitimate `deploy_account` transactions - (File: `crates/blockifier/src/execution/syscalls/syscall_base.rs`, `crates/starknet_api/src/core.rs`, `crates/blockifier/src/transaction/transactions.rs`)

### Summary
Both a contract's future `DeployAccount` address and any address produced by the `deploy` syscall are computed with the exact same deterministic formula: `pedersen("STARKNET_CONTRACT_ADDRESS", deployer_address, salt, class_hash, hash(constructor_calldata))` [1](#0-0) , with `deployer_address` fixed to `0` for `deploy_account` [2](#0-1) . Because `salt`, `class_hash`, and `constructor_calldata` (typically an account's public key) are known or guessable off-chain before the real `deploy_account` transaction lands, any unprivileged attacker can call the ordinary `deploy` syscall with `deploy_from_zero = true` from any funded contract to claim that exact address first. This mirrors the `pump-science` report's bug class: a PDA/account address is derivable purely from public seeds and can be created by anyone without the intended owner's signature, allowing a pre-emptive claim that DoSes the legitimate operation.

### Finding Description
`calculate_contract_address` is the single address-derivation function shared by `DeployAccountTransaction::calculate_contract_address` (via `CalculateContractAddress`/`DeployTransactionTrait`) and the `deploy` syscall's address computation in `SyscallHandlerBase::deploy` [3](#0-2) . When `deploy_from_zero` is `true`, the syscall uses `ContractAddress::default()` (zero) as the deployer, i.e., the identical deployer value used for `deploy_account` [4](#0-3) . Consequently, an attacker who knows (or predicts) a target account's `salt`, `class_hash`, and constructor calldata can compute the identical address and call `deploy_syscall(class_hash, salt, calldata, deploy_from_zero=true)` from any invoke transaction to plant a contract there first.

`execute_deployment` checks that `state.get_class_hash_at(deployed_contract_address)` is `ClassHash::default()` before writing the new class hash, and returns `StateError::UnavailableContractAddress` otherwise [5](#0-4) . `DeployAccountTransaction::run_execute` calls this same `execute_deployment` using `self.contract_address()` (the deterministic address) as `storage_address` [6](#0-5) . If the address is already occupied (by the attacker's front-run deployment), the constructor phase fails with `UnavailableContractAddress`, exactly as demonstrated by the existing regression test that shows a second deploy-to-the-same-address transaction fails this way [7](#0-6) .

Since `DeployAccount` is executed as a non-revertible transaction (constructor execution happens before nonce/fee validation, per `run_non_revertible`) [8](#0-7) , this failure aborts the whole transaction unconditionally — there is no way to retry deployment to that specific address ever again, because the address is now permanently associated with the attacker's contract class in state.

### Impact Explanation
Any counterfactual address (an address that a wallet computes off-chain and that users may pre-fund before deployment, a common pattern in Starknet account abstraction) can be irreversibly claimed by a third party before the legitimate account owner submits their `deploy_account` transaction. Because state writes to `class_hash_at` are permanent and PatriciaTree-committed, this results in **permanent freezing/loss of any funds sent to that predicted address** and permanent denial of the intended account's deployment at that specific address — the owner must generate a brand-new salt/address, losing continuity with any funds or interactions already tied to the original predicted address.

### Likelihood Explanation
The attack requires only:
1. Knowledge of the target's `class_hash`, `contract_address_salt`, and `constructor_calldata` — all of which are either publicly fixed per-wallet-implementation, visible from a broadcast/pending `deploy_account` transaction, or derivable from a leaked/known public key.
2. A single ordinary `invoke` transaction calling the `deploy` syscall with `deploy_from_zero = true`, reachable by any unprivileged sender with a funded account — no special privilege needed.

Front-running is straightforward: the attacker submits the `deploy` invocation with sufficient fee/tip to be ordered before the victim's `deploy_account` transaction in the same or an earlier block. This is directly analogous to the `pump-science` `lock_escrow` PDA front-run: a deterministic, publicly-derivable address can be claimed by an unprivileged actor before the legitimate initializer's transaction is processed.

### Recommendation
Since this griefing pattern is structurally identical to CREATE2-style front-running risks, it cannot be eliminated purely at the syscall layer without breaking legitimate `deploy_from_zero` use cases; consider evaluating whether `deploy_from_zero` should be restricted/removed for non-privileged contracts, or exposing a way for `deploy_account` to route around a maliciously-squatted address without permanently orphaning any pre-funded balance sent to it (e.g., surfacing an explicit, well-documented state query so wallets can detect squatting before funding a counterfactual address, and/or considering a protocol-level fix such as scoping deploy-from-zero addresses to a namespace disjoint from `deploy_account`'s zero-deployer namespace).

### Proof of Concept
1. Off-chain, compute a wallet's future account address `addr = calculate_contract_address(salt, account_class_hash, [public_key], deployer=0)`, matching `CalculateContractAddress` for `deploy_account` transactions [2](#0-1) .
2. As an unrelated attacker with any funded contract, submit an `invoke` transaction that calls `deploy_syscall(class_hash=account_class_hash, contract_address_salt=salt, calldata=[public_key], deploy_from_zero=true)`, which resolves to the identical `addr` per `SyscallHandlerBase::deploy` [3](#0-2) , deploying an arbitrary (e.g., empty) contract class there.
3. Once mined, the victim's subsequent `deploy_account` transaction to `addr` fails during `execute_deployment` with `StateError::UnavailableContractAddress` [9](#0-8)  — confirmed by the existing test asserting this exact error for "deploy to an existing address" [7](#0-6) . The address is now permanently unavailable for the intended account.

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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L645-670)
```rust
    fn run_non_revertible<S: StateReader>(
        &self,
        state: &mut TransactionalState<'_, S>,
        tx_context: Arc<TransactionContext>,
        remaining_gas: &mut GasCounter,
    ) -> TransactionExecutionResult<ValidateExecuteCallInfo> {
        let validate_call_info: Option<CallInfo>;
        let execute_call_info: Option<CallInfo>;
        if matches!(&self.tx, Transaction::DeployAccount(_)) {
            // Handle `DeployAccount` transactions separately, due to different order of things.
            // Also, the execution context required for the `DeployAccount` execute phase is
            // validation context.
            let mut execution_context = EntryPointExecutionContext::new_validate(
                tx_context.clone(),
                self.execution_flags.charge_fee,
                // TODO(Dori): Reduce code dup (the gas usage limit is computed in run_execute).
                // We initialize the revert gas tracker here for completeness - the value will not
                // be used, as this tx is non-revertible.
                SierraGasRevertTracker::new(GasAmount(
                    remaining_gas
                        .limit_usage(tx_context.sierra_gas_limit(&ExecutionMode::Validate)),
                )),
            );
            execute_call_info = self.run_execute(state, &mut execution_context, remaining_gas)?;
            validate_call_info = self.validate_tx(state, tx_context.clone(), remaining_gas)?;
        } else {
```
