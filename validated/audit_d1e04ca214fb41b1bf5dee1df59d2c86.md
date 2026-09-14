### Title
Global contract deployed in `AccountId` mode lets its owner unilaterally rewrite the code executing under every account that opted to `UseGlobalContractAction` it, with no consent, timelock, or warning at use-time - ([File: runtime/runtime/src/global_contracts.rs])

### Summary
The y2k-finance finding is that `Vault.changeController()` lets an admin unilaterally repoint a privileged role (`controller`) used to move vault funds (`sendTokens()`), with no timelock or on-chain warning to depositors who already trusted the vault. The structural analog in nearcore is the `GlobalContractDeployMode::AccountId` mechanism: an account that deploys a global contract "by account id" can redeploy (overwrite) that contract's code at any time, and every account that has attached itself to that contract via `UseGlobalContractAction`/`AccountContract::GlobalByAccount` will silently start running the new code the next time it is invoked - with no re-confirmation from the using account.

### Finding Description
`DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId` is documented as explicitly mutable: "Contract is deployed under the owner account id... This allows the owner to update the contract for all its users." [1](#0-0) 

`action_deploy_global_contract` simply charges storage cost and calls `initiate_distribution`, which derives the global contract's identifier as `GlobalContractIdentifier::AccountId(account_id)` for that mode — i.e. the same identifier is reused on every redeploy by that owner account: [2](#0-1) 

`apply_distribution_current_shard` then simply overwrites the stored code for that identifier (`state_update.set(trie_key, ...)`), gated only by a nonce-freshness check to prevent stale distribution replays — there is no check that the redeploy is somehow blocked while other accounts depend on it, and no on-chain signal to dependent accounts: [3](#0-2) 

On the consumer side, `use_global_contract` lets any account attach to any existing global contract identifier (including one owned/controlled by an unrelated account) via `UseGlobalContractAction`, setting `AccountContract::GlobalByAccount(id)` on the caller's own account with no additional authorization from — or ongoing linkage/warning to — the referenced owner beyond existence of the code: [4](#0-3) 

Because the WASM that becomes `AccountContract::GlobalByAccount(id)` executes in the *using* account's own execution context (its own `current_account_id`), a contract that includes a native `Transfer`/withdrawal-style promise (analogous to `sendTokens()`) has full ability to move that account's own NEAR balance once invoked by any external `FunctionCall` transaction. The owner of the `AccountId`-mode global contract can therefore push a new version of the code — at any time, unilaterally, without any timelock or on-chain warning — that adds such a draining method, and it becomes live for every account that had previously opted into `UseGlobalContractAction` for that identifier, exactly mirroring the `changeController()`/`sendTokens()` pattern: a role change (redeploy) instantly repoints what code is authorized to move funds out of dependent accounts.

### Impact Explanation
Any account that references a mutable (`AccountId`-mode) global contract is exposed to unilateral, warning-free code changes by that contract's deployer at any future point, including after the "using" account has accumulated balance. If the deployer becomes malicious or is compromised, it can push code that transfers the balance of every dependent account to an attacker-controlled address the next time any (even unprivileged) caller triggers the new method via an ordinary `FunctionCall` transaction — concrete unauthorized value movement across potentially many accounts, with no cooling-off period.

### Likelihood Explanation
Reachable purely by ordinary, unprivileged transactions: (1) deploy a global contract in `AccountId` mode, (2) have (or trick) other accounts into `UseGlobalContractAction` referencing it, (3) redeploy the same identifier with malicious code, (4) send a normal `FunctionCall` transaction to trigger the drain. No validator, network, or operator privilege is required — only account ownership of the global contract identifier, which is by design available to any signer. The main mitigating factor is that a using account must voluntarily opt in via `UseGlobalContractAction`, and the design intent (per the code comment) is that this mode is understood to be mutable; nonetheless nothing in the protocol enforces disclosure, timelocks, or re-confirmation at redeploy time, which is precisely the gap the y2k-finance report criticizes.

### Recommendation
- Add a redeploy delay/timelock for `GlobalContractDeployMode::AccountId` distributions so dependent accounts have a window to detect and detach (via a fresh `UseGlobalContractAction`/`DeployContract`) before new code becomes active.
- Emit an explicit on-chain event/log when a global contract identifier referenced by other accounts is redeployed, so indexers/wallets can surface a warning to affected accounts.
- Consider recording, at `use_global_contract` time, a commitment (e.g., code hash) that a dependent account expects, and require the account to explicitly re-opt-in when the referenced `AccountId` contract's code changes, rather than silently inheriting whatever the latest deploy is.

### Proof of Concept
1. Account `owner.near` deploys a global contract in `AccountId` mode containing only benign logic: `SignedTransaction::deploy_global_contract(..., GlobalContractDeployMode::AccountId)` as exercised in `test_global_contract_update` [5](#0-4) .
2. Account `victim.near` opts in with `UseGlobalContractAction { contract_identifier: GlobalContractIdentifier::AccountId(owner.near) }`, which sets `victim.near`'s `AccountContract::GlobalByAccount("owner.near")` per `use_global_contract` [6](#0-5) .
3. `victim.near` accumulates a NEAR balance over time while using the shared contract for its intended (benign) functionality.
4. `owner.near` redeploys the same `AccountId`-mode identifier with new code that adds a public method invoking `Promise::new(env::current_account_id()).transfer(...)` to an attacker address; `apply_distribution_current_shard` overwrites the stored code for that identifier with no check on dependents [7](#0-6) .
5. Once the redeploy distribution receipt lands on `victim.near`'s shard, any unprivileged caller sends a normal `FunctionCall` transaction invoking the new drain method against `victim.near`; the code executes under `victim.near`'s own account authority and transfers its balance out, with `victim.near` never having signed off on the new code.

### Citations

**File:** core/primitives/src/action/mod.rs (L140-143)
```rust
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
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

**File:** runtime/runtime/src/global_contracts.rs (L143-158)
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
```

**File:** runtime/runtime/src/global_contracts.rs (L204-227)
```rust
    let is_nonce_fresh = check_and_update_nonce(global_contract_data, &identifier, state_update)?;
    if !is_nonce_fresh {
        return Ok(0);
    }

    let config = apply_state.config.wasm_config.clone();
    let trie_key = TrieKey::GlobalContractCode { identifier };
    let code_len = global_contract_data.code().len() as u64;
    state_update.set(trie_key, global_contract_data.code().to_vec());

    // Record the deploy so a same-chunk call can find the code without a warm cache.
    let code_hash = match global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => Some(*hash),
        GlobalContractIdentifier::AccountId(_) => None,
    };
    if ProtocolFeature::GlobalContractSameChunkCallFix.enabled(apply_state.current_protocol_version)
    {
        state_update.record_global_contract_deploy(ContractCode::new(
            global_contract_data.code().to_vec(),
            code_hash,
        ));
    }

    state_update.commit(StateChangeCause::ReceiptProcessing { receipt_hash: receipt.get_hash() });
```

**File:** test-loop-tests/src/tests/global_contracts.rs (L72-106)
```rust
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
