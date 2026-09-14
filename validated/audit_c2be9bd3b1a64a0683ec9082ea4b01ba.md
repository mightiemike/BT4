### Title
Mutable "AccountId"-mode global contracts let the deployer silently rewrite the code executed against every account (including deterministic accounts) that references it — ([File: runtime/runtime/src/global_contracts.rs])

### Summary
`GlobalContractDeployMode::AccountId` lets any account act as a "code publisher" that other accounts reference indirectly via `AccountContract::GlobalByAccount(id)` instead of storing a snapshot of the code. Because the reference is resolved to whatever code currently lives under that publisher's account id at call time, the publisher can redeploy completely different code at any point after users have opted in (`UseGlobalContractAction`) or, more seriously, after users have created `DeterministicStateInit` accounts and funded them, trusting the documented invariant that "Deterministic accounts have a fixed code and fixed initial state." This mirrors the Biconomy `setLpToken` bug: a mutable pointer that downstream users implicitly and permanently trust, with no migration protection or one-time-set guarantee, letting the original "owner" unilaterally redefine the logic (and thus the fate of funds/storage) of every dependent account.

### Finding Description
- `GlobalContractDeployMode` has two modes; `AccountId` is explicitly documented to let "the owner update the contract for all its users": [1](#0-0) .
- When an account calls `UseGlobalContract` (or a `DeterministicStateInitAction` references `GlobalContractIdentifier::AccountId`), the runtime does not copy/pin the current code; it stores only a pointer, `AccountContract::GlobalByAccount(id)`, that is dereferenced dynamically at execution time via `use_global_contract`: [2](#0-1) .
- The publishing account can later submit a new `DeployGlobalContractAction` with `AccountId` mode, which re-associates the same `GlobalContractIdentifier::AccountId(account_id)` with new code: [3](#0-2) . Distribution is idempotent/ordered by nonce only to prevent stale overwrites — it does not prevent legitimate, intentional overwrites of the code by the original publisher: [4](#0-3) .
- The end-to-end effect (redeploy changes behavior for all consumers already pointing at the account id) is directly demonstrated by `test_global_contract_update`, where a trivial no-op contract is later replaced by `rs_contract`, changing what function calls succeed for accounts that already called `UseGlobalContract` beforehand: [5](#0-4) .
- Crucially, this same mutable reference mechanism underlies `DeterministicStateInitAction`, whose code field can be `GlobalContractIdentifier::AccountId`: [6](#0-5) . Yet the protocol documentation states deterministic accounts have "fixed code and fixed initial state," which is contradicted for the `AccountId` deploy mode: [7](#0-6) .

### Impact Explanation
Any unprivileged user can deploy a global contract in `AccountId` mode with innocuous code, wait for other users to reference it via `UseGlobalContractAction` or to create/fund `DeterministicStateInit` accounts against it (depositing NEAR/storage data trusting the "fixed code" guarantee), and then redeploy new code under the same account id containing a backdoor (e.g. a hidden withdraw/transfer method targeting the attacker). Because `AccountContract::GlobalByAccount` is resolved dynamically, this new code executes with full authority over every dependent account's balance and contract storage the next time those accounts receive a `FunctionCall`. This is a concrete unauthorized-value-movement / permanently-frozen-funds vector reachable purely through ordinary transactions, with no path for the affected accounts to opt out (an account's code identity, once set to `GlobalByAccount`, cannot self-detect or block the swap).

### Likelihood Explanation
Exploitation requires only standard, unprivileged transactions available to any signer: `DeployGlobalContractAction` (twice, initial + malicious redeploy), and getting other users to naturally opt into referencing that code via `UseGlobalContract` or `DeterministicStateInitAction` (e.g., by presenting it as a useful sharded-contract library, per the deterministic-account/NEP-616 use case demonstrated in `test-loop-tests/src/tests/deterministic_account_id.rs`). No validator, operator, or network-layer privilege is needed — matching the "unprivileged owner of a shared resource" pattern from the original report.

### Recommendation
- For `DeterministicStateInitAction`, require (or strongly recommend/enforce by protocol policy) `GlobalContractIdentifier::CodeHash` rather than `AccountId`, since the documented "fixed code" guarantee for deterministic accounts is otherwise false.
- For `UseGlobalContractAction` consumers in general, consider pinning to the code hash observed at use-time (recording it in account state) so a later redeploy under the same `AccountId` cannot silently change behavior for existing users without an explicit re-`UseGlobalContract` action from them.
- At minimum, clearly surface (via RPC/view calls and wallets) that an account's `global_contract_account_id` reference is a live, mutable pointer, distinct from `global_contract_hash`, so integrators can assess this trust assumption before depositing funds against `GlobalByAccount`-referencing accounts.

### Proof of Concept
1. Attacker submits `DeployGlobalContractAction { code: benign_code, deploy_mode: AccountId }` from `attacker.near`.
2. Victim account calls `UseGlobalContractAction { contract_identifier: AccountId(attacker.near) }`, or creates/funds a `DeterministicStateInitAction` whose `state_init.code = GlobalContractIdentifier::AccountId(attacker.near)`, depositing NEAR/storage into the resulting account (as shown in `test-loop-tests/src/tests/deterministic_account_id.rs` sharded-contract user setup).
3. Attacker submits a second `DeployGlobalContractAction { code: malicious_code, deploy_mode: AccountId }` from the same `attacker.near`, which `initiate_distribution` propagates and overwrites the code stored under `GlobalContractIdentifier::AccountId(attacker.near)` (as validated functionally by `test_global_contract_update`).
4. Any subsequent `FunctionCall` receipt targeting the victim's account now executes `malicious_code` with full authority over the victim account's balance/storage (per `use_global_contract`'s dynamic `AccountContract::GlobalByAccount` resolution), enabling the attacker to drain funds or corrupt state that the victim believed was governed by fixed, previously-audited code.

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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L268-299)
```rust
/// Test that nonce-based idempotency prevents stale overwrites during global contract updates.
///
/// Deploys a trivial contract first (AccountId mode), waits for distribution,
/// then deploys rs_contract (AccountId mode) with a higher auto-incremented nonce.
/// Verifies all shards have the newer version by calling a function that only
/// exists in the rs_contract.
#[test]
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_global_contract_nonce_prevents_stale_overwrite() {
    init_test_logger();
    let mut env = GlobalContractsReshardingTestEnv::setup();

    let deploy_user = env.users[0].clone();

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

**File:** runtime/runtime/src/deterministic_account_id.rs (L142-154)
```rust
fn deploy_deterministic_account(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    state_init: &DeterministicAccountStateInit,
    result: &mut ActionResult,
    storage_usage_config: &StorageUsageConfig,
) -> Result<(), RuntimeError> {
    // Step 1: set contract code (includes storage usage accounting)
    use_global_contract(state_update, account_id, account, state_init.code(), result)?;
    if result.result.is_err() {
        return Ok(());
    }
```

**File:** docs/DataStructures/Account.md (L138-146)
```markdown
## Deterministic accounts

Deterministic accounts are an advanced kind of implicit account.
A normal implicit account has a fixed access key that is implicitly associated with it.
Deterministic accounts have a fixed code and fixed initial state associated with it.

### State-initializing data of deterministic accounts

The initial state of a deterministic account is fully defined by an instance of `DeterministicAccountStateInit`.
```
