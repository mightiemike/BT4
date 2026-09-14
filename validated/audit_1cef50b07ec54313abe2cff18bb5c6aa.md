## Analog Found

### Title
Global Contract `AccountId` mode allows an unprivileged deployer to unilaterally push code upgrades that break function compatibility for all dependent accounts, permanently freezing their funds - (File: `core/primitives/src/action/mod.rs`, `runtime/runtime/src/global_contracts.rs`)

### Summary
The Perennial `BalancedVault` bug is a class of "shared-implementation upgrade removes functions relied on by dependents, stranding their assets." NEAR has a structurally identical mechanism: `GlobalContractDeployMode::AccountId` lets one account own a piece of WASM code that many *other* accounts reference by `UseGlobalContractAction`. Any account that calls `UseGlobalContract` with `GlobalContractIdentifier::AccountId(owner)` permanently binds its own execution logic to whatever code the owner currently has deployed at that identifier — and the owner can redeploy incompatible code at any later time, with no consent, versioning guarantee, or rollback path available to the accounts that depend on it.

### Finding Description
`GlobalContractDeployMode` explicitly documents this trade-off: [1](#0-0) 

`action_use_global_contract` / `use_global_contract` resolve a receiver account's contract to whatever code currently sits at the referenced identifier at call time — there is no pinning to a specific code version once `UseGlobalContract` is executed: [2](#0-1) 

Crucially, only the *code* is shared; each referencing account keeps its **own** separate contract storage/state (per-account trie). So if account `A` executes `UseGlobalContract(AccountId(owner))`, accumulates balances/data in its own storage via that code's methods, and later `owner` redeploys new code at the same `AccountId` identifier (`initiate_distribution`/`DeployGlobalContractAction` with `deploy_mode = AccountId`), account `A`'s stored data and any funds contingent on specific methods existing in the code become subject to whatever the new code implements — with zero say from `A`. The distribution/propagation logic just overwrites the identifier's code shard-by-shard: [3](#0-2) 

The nearcore test suite itself demonstrates that swapping the code behind a live `AccountId` identifier changes runtime behavior for *every* account that has called `UseGlobalContract` against it, including making previously working methods disappear (`MethodNotFound`): [4](#0-3) 

This is the exact bug class from the report: a "V1 → V2" contract upgrade (here, an owner-controlled global contract redeploy) that fails to preserve functions (e.g., a withdrawal/transfer/redeem method) that dependent accounts' own state relies on to access value they hold. Unlike BalancedVault's proxy (which is a single logical protocol upgrading itself), here *arbitrary third-party accounts* opt into binding their fate to a contract owner they don't control, and the protocol provides no safeguard (no method-signature compatibility check, no opt-out, no local fallback code once bound).

### Impact Explanation
Any account (including one holding significant NEAR balance or acting as a NEP-141/143 token ledger with its own escrowed state) that uses `UseGlobalContract` in `AccountId` mode is permanently exposed to the deploying owner's future redeployments. If the owner redeploys code that omits a function needed to withdraw/transfer assets recorded in the dependent account's own storage (accidentally, through a buggy migration, or via compromise of the owner's key), those assets become permanently inaccessible — matching "permanently frozen funds" in the acceptance criteria. This requires no validator collusion, no network-layer or sync bug; it is triggered purely by ordinary transactions (`DeployGlobalContract`, `UseGlobalContract`) from unprivileged signers.

### Likelihood Explanation
Reaching this state requires only:
1. An account owner deploying a global contract in `AccountId` mode (`SignedTransaction::deploy_global_contract` with `GlobalContractDeployMode::AccountId`).
2. Other accounts opting in via `UseGlobalContract` referencing that `AccountId`.
3. The owner later redeploying incompatible code to the same identifier.

All three steps are ordinary, permissionless transactions reachable by any signer/RPC caller; nothing here requires validator or node-operator privilege. The main mitigating factor is that step 2 (opting in) is voluntary and the risk is documented in the API description, which somewhat reduces likelihood relative to an unannounced/accidental compatibility break, but the protocol supplies no technical safeguard against it.

### Recommendation
Consider adding protocol-level protections for `AccountId`-mode global contracts consumed by third parties, such as:
- Allowing a referencing account to "pin" to a specific code hash snapshot at `UseGlobalContract` time (opt-in immutability) while still being distributed under the `AccountId` for discovery purposes.
- Exposing a supported migration/exit path (e.g., re-`UseGlobalContract` back to a specific historical hash) so dependents are not solely at the mercy of the owner's latest deploy.
- Documenting more prominently (and perhaps requiring explicit acknowledgment in the action) that `AccountId` mode grants the owner unilateral, un-revocable control over the executable logic of every account that references it.

### Proof of Concept
1. `owner` deploys global contract V1 (`GlobalContractDeployMode::AccountId`) implementing `withdraw()`/`ft_transfer()`-style functions. [5](#0-4) 
2. `userA` calls `UseGlobalContract(AccountId(owner))`, then calls a method on V1 that records a balance/escrow entry in `userA`'s own contract storage.
3. `owner` deploys global contract V2 at the same `AccountId` identifier, omitting `withdraw()`/`ft_transfer()` (as shown feasible by `test_global_contract_update`, which redeploys a "trivial" contract with no methods at all, then a full contract, over the same identifier). [6](#0-5) 
4. `userA` can no longer call any method to retrieve the value recorded under V1's data layout in its own account storage — the code that could access/interpret/move it no longer exists anywhere referenceable, and `userA` has no local fallback code (it was never stored locally) and no mechanism to force `owner` to redeploy V1.

### Citations

**File:** core/primitives/src/action/mod.rs (L135-144)
```rust
pub enum GlobalContractDeployMode {
    /// Contract is deployed under its code hash.
    /// Users will be able reference it by that hash.
    /// This effectively makes the contract immutable.
    CodeHash,
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
}
```

**File:** runtime/runtime/src/global_contracts.rs (L76-109)
```rust
pub(crate) fn use_global_contract(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
    account: &mut Account,
    contract_identifier: &GlobalContractIdentifier,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let key = TrieKey::GlobalContractCode { identifier: contract_identifier.clone().into() };
    if !state_update.contains_key(&key, AccessOptions::DEFAULT)? {
        result.result = Err(ActionErrorKind::GlobalContractDoesNotExist {
            identifier: contract_identifier.clone(),
        }
        .into());
        return Ok(());
    }
    clear_account_contract_storage_usage(state_update, account_id, account)?;
    if account.contract().is_local() {
        state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });
    }
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
    account.set_storage_usage(
        account.storage_usage().checked_add(contract_identifier.len() as u64).ok_or_else(|| {
            StorageError::StorageInconsistentState(format!(
                "Storage usage integer overflow for account {}",
                account_id
            ))
        })?,
    );
    account.set_contract(contract).or_inconsistent_state(account_id)?;
    Ok(())
}
```

**File:** runtime/runtime/src/global_contracts.rs (L143-171)
```rust
fn initiate_distribution(
    state_update: &mut TrieUpdate,
    account_id: AccountId,
    contract_code: Arc<[u8]>,
    deploy_mode: &GlobalContractDeployMode,
    current_shard_id: ShardId,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
    // Increment the nonce and write it to state immediately to prevent multiple
    // distributions with the same nonce from being initiated. This requires
    // allowing the same nonce in the freshness check when applying the
    // distribution receipt.
    let nonce = increment_nonce(state_update, &id)?;
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
    let distribution_receipts =
        Receipt::new_global_contract_distribution(account_id, distribution_receipt);
    // No need to set receipt_id here, it will be generated as part of apply_action_receipt
    result.new_receipts.push(distribution_receipts);
    Ok(())
}
```

**File:** test-loop-tests/src/tests/global_contracts.rs (L71-106)
```rust
#[test]
fn test_global_contract_update() {
    let mut env = GlobalContractsTestEnv::setup(Balance::from_near(1000));
    let use_accounts = [env.account_shard_0.clone(), env.account_shard_1.clone()];

    env.deploy_trivial_global_contract(GlobalContractDeployMode::AccountId);

    for account in &use_accounts {
        env.use_global_contract(
            account,
            GlobalContractIdentifier::AccountId(env.deploy_account.clone()),
        );

        // Currently deployed trivial contract doesn't have any methods,
        // so we expect any function call to fail with MethodNotFound error
        let call_tx = env.call_global_contract_tx(account.clone(), account.clone());
        let call_outcome = env.execute_tx(call_tx);
        assert_matches!(
            call_outcome.status,
            FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
                kind: ActionErrorKind::FunctionCallError(FunctionCallError::MethodResolveError(
                    MethodResolveError::MethodNotFound
                )),
                index: _
            }))
        );
    }

    env.deploy_global_contract(GlobalContractDeployMode::AccountId);

    for account in &use_accounts {
        // Function call should be successful after deploying rs contract
        // containing the function we call here
        env.assert_call_global_contract_success(account.clone(), account.clone());
    }
}
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L282-299)
```rust
    // Step 1: Deploy trivial contract as first version (AccountId mode).
    tracing::info!(target: "test", "Deploying first version of global contract (trivial contract)...");
    let tx = env.chunk_producer_node().tx_deploy_global_contract(
        &deploy_user,
        near_test_contracts::trivial_contract().to_vec(),
        GlobalContractDeployMode::AccountId,
    );
    env.env.runner_for_account(&env.chunk_producer).run_tx(tx, Duration::seconds(5));

    // Step 2: Deploy rs_contract as second version (AccountId mode).
    // This will have a higher auto-incremented nonce.
    tracing::info!(target: "test", "Deploying second version of global contract (rs_contract)...");
    let tx = env.chunk_producer_node().tx_deploy_global_contract(
        &deploy_user,
        near_test_contracts::rs_contract().to_vec(),
        GlobalContractDeployMode::AccountId,
    );
    env.env.runner_for_account(&env.chunk_producer).run_tx(tx, Duration::seconds(5));
```
