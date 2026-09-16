Now I have enough to confirm the mechanism end-to-end: a `deploy_account` transaction's target address is deterministically derived from public fields (`class_hash`, `contract_address_salt`, `constructor_calldata`) visible once the transaction is broadcast/pending, and any observer can front-run it by invoking the `deploy` syscall from an already-deployed account to occupy that exact address before the victim's transaction executes.

### Title
Deploy-account address can be front-run and squatted by an unprivileged attacker observing the mempool, causing denial-of-service of the victim's account deployment - (File: crates/blockifier/src/execution/execution_utils.rs)

### Summary
A `DeployAccountTransaction`'s target contract address is fully deterministic from public fields (`class_hash`, `contract_address_salt`, `constructor_calldata`, and the fixed zero deployer address). An attacker who observes a pending `deploy_account` transaction in the mempool (or gossip) can compute the same address off-chain and race a normal `invoke` transaction that calls the `deploy` syscall with `deploy_from_zero=true` and the identical `(class_hash, salt, calldata)` triple, from any already-deployed account. If the attacker's transaction lands first, the victim's `deploy_account` transaction fails with `StateError::UnavailableContractAddress` when it later executes, permanently denying that specific address to the intended owner unless they choose a new salt.

### Finding Description
The contract address for a `deploy_account` transaction is computed with a fixed zero deployer address, making it a pure function of `(salt, class_hash, constructor_calldata)`: [1](#0-0) 

These fields (`class_hash`, `contract_address_salt`, `constructor_calldata`) are all part of the public, unsigned transaction body, so once the transaction is broadcast to the mempool/gossip network, any observer can compute the exact target address before it is included in a block.

Separately, the generic `deploy` syscall lets any already-deployed contract deploy a new contract "from zero" (i.e. with the same deployer-independent address formula used for `deploy_account`) by simply supplying the matching `class_hash`, salt, and calldata: [2](#0-1) 

Both `execute_deployment` (used for `deploy_account`) and the `deploy` syscall path share the same address-allocation logic, which errors if the address already has a non-default class hash set: [3](#0-2) 

Consequently, if the attacker's `deploy`-syscall invoke transaction is sequenced first (by paying a higher tip/fee, or simply being executed earlier), the victim's later `deploy_account` transaction hits `UnavailableContractAddress` and fails, as directly exercised by an existing regression test: [4](#0-3) 

Because contract-address computation is a public, permissionless algorithm (analogous to Ethereum's CREATE2), and mempool contents are visible to unprivileged observers before block inclusion, this exactly matches the reported bug class: a party can monitor the mempool for a pending, deterministically-derived claim (there, a referral code; here, a deployment address) and front-run it with their own transaction.

### Impact Explanation
When the front-run occurs, the victim's `deploy_account` transaction execution returns a hard `Err` (not a revert), so per the existing test `test_fail_deploy_account`, no fee is charged and no nonce is bumped: [5](#0-4) 

The block builder classifies such a failing transaction as rejected and excludes it from the block: [6](#0-5) 

The practical impact is a denial-of-service against the specific counterfactual address the victim intended to deploy to: the account can never be deployed at that address (the class hash slot is now permanently occupied by the attacker's dummy contract), forcing the victim to choose a new salt and, more importantly, invalidating any off-chain assumptions, pre-funding, or counterfactual-address-based integrations (e.g., paymasters, exchanges pre-funding a computed address) that depended on that specific address being controlled by the victim's account contract. This is a permanent, unauthorized denial of the intended account action at that address.

### Likelihood Explanation
This requires the attacker to observe a pending `deploy_account` transaction in the mempool/gossip network and to already control a deployed account with funds, then submit a competing `invoke` transaction calling `deploy` with the same public salt/class_hash/calldata and a marginally higher tip/priority. All required data (`class_hash`, `contract_address_salt`, `constructor_calldata`) are part of the unsigned transaction fields sent to the mempool, so the attack is straightforward to script and requires no privileged access; likelihood is driven only by network monitoring and transaction-ordering incentives (tip escalation) available to any regular user.

### Recommendation
Because this mirrors a well-known address-squatting class shared with CREATE2-style deployments, consider documenting the risk and/or providing wallet/SDK guidance to randomize salts (reducing predictability) and to detect `UnavailableContractAddress` failures early. At the protocol level, mitigation options mirror the report's original suggestion: incorporate unpredictable/private entropy (e.g., derived from account public key plus a client-chosen random salt not easily guessable, or commit-reveal schemes) so that the deployment address cannot be trivially front-run purely by observing mempool contents.

### Proof of Concept
1. Victim broadcasts a `DeployAccountTransaction` with `class_hash = C`, `contract_address_salt = S`, `constructor_calldata = D`. The resulting address `A = calculate_contract_address(S, C, D, ContractAddress::ZERO)` is computable by anyone from the pending transaction, per [7](#0-6) .
2. Attacker, controlling any already-deployed account, submits an `invoke` transaction calling a contract that performs `deploy_syscall(class_hash=C, contract_address_salt=S, calldata=D, deploy_from_zero=true)`, reproducing the identical address `A` via `syscall_base::deploy` per [2](#0-1) , with a higher tip so it is sequenced first.
3. Once the attacker's transaction executes, `A`'s class hash is set to a non-default value.
4. When the victim's `deploy_account` transaction subsequently executes, `execute_deployment` detects `current_class_hash != ClassHash::default()` and returns `StateError::UnavailableContractAddress(A)`, causing the transaction to fail entirely (no fee charged, no nonce bump), matching the existing regression test at [4](#0-3) . The victim can never deploy their account at address `A`.

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

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L812-853)
```rust
/// Tests that failing account deployment should not change state (no fee charge or nonce bump).
fn test_fail_deploy_account(
    block_context: BlockContext,
    #[case] cairo_version: CairoVersion,
    #[values(TransactionVersion::ONE, TransactionVersion::THREE)] tx_version: TransactionVersion,
) {
    let chain_info = &block_context.chain_info;
    let faulty_account_feature_contract = FeatureContract::FaultyAccount(cairo_version);
    let state = &mut test_state(chain_info, BALANCE, &[(faulty_account_feature_contract, 0)]);

    // Create and execute (failing) deploy account transaction.
    let deploy_account_tx =
        create_account_tx_for_validate_test_nonce_0(FaultyAccountTxCreatorArgs {
            tx_type: TransactionType::DeployAccount,
            tx_version,
            scenario: INVALID,
            class_hash: faulty_account_feature_contract.get_class_hash(),
            max_fee: BALANCE,
            resource_bounds: default_l1_resource_bounds(),
            ..Default::default()
        });
    let fee_token_address = chain_info.fee_token_address(&deploy_account_tx.fee_type());

    let deploy_address = match &deploy_account_tx.tx {
        ApiExecutableTransaction::DeployAccount(deploy_tx) => deploy_tx.contract_address(),
        _ => unreachable!("deploy_account_tx is a DeployAccount"),
    };
    fund_account(chain_info, deploy_address, Fee(BALANCE.0 * 2), &mut state.state);

    let initial_balance = state.get_fee_token_balance(deploy_address, fee_token_address).unwrap();

    let error = deploy_account_tx.execute(state, &block_context).unwrap_err();
    // Check the error is as expected. Assure the error message is not nonce or fee related.
    check_tx_execution_error_for_invalid_scenario!(cairo_version, error, false);

    // Assert nonce and balance are unchanged, and that no contract was deployed at the address.
    assert_eq!(state.get_nonce_at(deploy_address).unwrap(), nonce!(0_u8));
    assert_eq!(
        state.get_fee_token_balance(deploy_address, fee_token_address).unwrap(),
        initial_balance
    );
    assert_eq!(state.get_class_hash_at(deploy_address).unwrap(), ClassHash::default());
```

**File:** crates/apollo_batcher/src/block_builder.rs (L708-716)
```rust
            Err(err) => {
                info!(
                    "Transaction {} failed to execute with error: {}.",
                    tx_hash,
                    err.log_compatible_to_string()
                );
                let is_new_entry = execution_data.rejected_tx_hashes.insert(tx_hash);
                assert!(is_new_entry, "Duplicate rejected transaction hash: {tx_hash}.");
            }
```
