## Analysis

The C4 report's root issue is that a party controls upgradeable *logic* that runs on top of storage/state a user has trusted to that logic, and can redeploy at will to insert a backdoor that manipulates the user's assets — without any fresh consent from the user beyond the original, one-time approval.

nearcore has a structurally identical trust primitive: **Global Contracts** deployed with `GlobalContractDeployMode::AccountId`. Any account can adopt such a contract via `UseGlobalContractAction { contract_identifier: GlobalContractIdentifier::AccountId(owner) }`, after which all function calls against that account run the code currently published under `owner`. Crucially, the owner of that global-contract account can redeploy new code under the same `AccountId` identifier at any later time via `DeployGlobalContractAction` with `deploy_mode: AccountId`, and this instantly changes the logic executed on behalf of *every account that has opted in* — with no re-confirmation required from those accounts. [1](#0-0) 

The runtime logic implementing this: `action_deploy_global_contract` lets the owner push new code under the `AccountId` identifier, and `use_global_contract` binds a using account's `AccountContract::GlobalByAccount(id)` reference to that mutable identifier rather than a fixed hash. [2](#0-1) [3](#0-2) 

This is documented as intentional: "This allows the owner to update the contract for all its users." [4](#0-3) [5](#0-4) 

Any state (e.g. token balances, allowance-like bookkeeping) an account keeps in its own contract storage remains attached to the account across a code swap — only the code reference is swapped, storage is preserved, matching the analog's pattern where the admin's new logic operates over pre-existing user-approved state.

### Title
Global contract owner can silently rewrite by-`AccountId` code referenced by dependent accounts, letting the owner drain assets held in their contract storage - (File: runtime/runtime/src/global_contracts.rs)

### Summary
Accounts can adopt shared contract code by reference (`UseGlobalContractAction` with `GlobalContractIdentifier::AccountId`). The referenced global contract's owner retains unilateral, unrestricted ability to redeploy new WASM under that same `AccountId` at any time (`DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId`), which is immediately and silently applied to every account that references it, without any additional signature, timelock, or consent step from those dependent accounts.

### Finding Description
`action_use_global_contract` binds an account's contract to `AccountContract::GlobalByAccount(owner_id)` — a live pointer, not a content hash. [6](#0-5) 

`action_deploy_global_contract` allows the `owner_id` account (an ordinary, unprivileged account — anyone can deploy a global contract) to overwrite the code stored under that identifier at will, and the new code is distributed to all shards and takes effect for every account using `GlobalContractIdentifier::AccountId(owner_id)`. [7](#0-6) [8](#0-7) 

This is the direct on-chain analog of an upgradeable proxy: a dependent account's execution logic is controlled by a third party (the global contract owner) who can change it after the fact, while the dependent account's own persistent contract storage (its balances, allowance bookkeeping, etc., analogous to the ERC-20 `allowance` in the report) is preserved across the swap and becomes subject to whatever the new code does with it.

### Impact Explanation
If a fungible-token contract, escrow contract, or any value-holding contract account adopts shared logic via `UseGlobalContract(AccountId)` (e.g. to save deployment/storage costs, as demonstrated by the sharded-contract test), the global contract owner can push a new version that adds a backdoor (e.g., an unauthenticated "sweep balances to attacker" method) and immediately compromise every account referencing it, moving out balances or minting tokens with no cooperation from the affected accounts. This is concrete unauthorized value movement across all dependent accounts simultaneously.

### Likelihood Explanation
Requires a hypothetical, stated assumption identical to the original report's characterization ("faulty governance"/trust in the global-contract owner): an account must have voluntarily opted into referencing a global contract by `AccountId` (as opposed to the immutable `CodeHash` mode), and the owner of that global contract must act maliciously or be compromised. Given `AccountId` mode is explicitly a supported, documented feature (not merely a misconfiguration), and is showcased for legitimate reuse cases (e.g. `runtime/near-test-contracts/sharded-contract`), the precondition is realistically reachable by any unprivileged deployer/user who chooses to trust a shared global contract owner.

### Recommendation
- Document prominently at the API/CLI level that `UseGlobalContract(AccountId)` grants the referenced owner perpetual, unilateral control over the using account's execution logic, and strongly recommend `CodeHash` mode (immutable) for any account holding user value.
- Consider adding an opt-in mechanism requiring the dependent account to re-confirm (e.g. re-submit `UseGlobalContractAction`) after the owner publishes a new version, rather than applying updates transparently.
- Consider exposing a way to pin to a specific historical code hash of an `AccountId`-mode global contract, or emit a distinguishable event/version marker that indexers and wallets can flag before code changes take effect for a given account.

### Proof of Concept
1. Account `owner.near` deploys a global contract in `AccountId` mode containing a legitimate NEP-141-like token implementation (`DeployGlobalContractAction { deploy_mode: AccountId }`).
2. Account `token.near` (holding real user balances in its own contract storage) calls `UseGlobalContractAction { contract_identifier: AccountId(owner.near) }`, adopting that code — analogous to users approving unlimited allowance to a trusted contract.
3. Users interact with `token.near`, accumulating balances in `token.near`'s persistent storage.
4. `owner.near` (a compromised/malicious "admin", exactly like the proxy admin in the original report) submits a new `DeployGlobalContractAction { deploy_mode: AccountId }` with modified WASM adding a `drain()` method that transfers all account balances in storage to an attacker-controlled account.
5. On distribution, `token.near`'s subsequent function calls run the new code and the attacker calls `drain()`, moving out all previously accumulated user balances — with no additional action, consent, or transaction from `token.near`'s original controller or its users, mirroring the C4 report's rug scenario where the proxy admin drains approved allowances.

### Citations

**File:** core/primitives/src/action/mod.rs (L133-142)
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

**File:** runtime/runtime/src/global_contracts.rs (L23-107)
```rust
pub(crate) fn action_deploy_global_contract(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    apply_state: &ApplyState,
    deploy_contract: &DeployGlobalContractAction,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let _span = tracing::debug_span!(target: "runtime", "action_deploy_global_contract").entered();

    let storage_cost = apply_state
        .config
        .fees
        .storage_usage_config
        .global_contract_storage_amount_per_byte
        .saturating_mul(deploy_contract.code.len() as u128);
    let Some(updated_balance) = account.amount().checked_sub(storage_cost) else {
        result.result = Err(ActionErrorKind::LackBalanceForState {
            account_id: account_id.clone(),
            amount: storage_cost,
        }
        .into());
        return Ok(());
    };
    result.tokens_burnt =
        result.tokens_burnt.checked_add(storage_cost).ok_or(IntegerOverflowError)?;
    account.set_amount(updated_balance);

    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;

    Ok(())
}

pub(crate) fn action_use_global_contract(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
    account: &mut Account,
    action: &UseGlobalContractAction,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let _span = tracing::debug_span!(target: "runtime", "action_use_global_contract").entered();
    use_global_contract(state_update, account_id, account, &action.contract_identifier, result)
}

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
    account.set_contract(contract);
    Ok(())
}
```

**File:** runtime/runtime/src/global_contracts.rs (L141-169)
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

**File:** runtime/runtime/src/global_contracts.rs (L189-233)
```rust
fn apply_distribution_current_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    state_update: &mut TrieUpdate,
) -> Result<Compute, RuntimeError> {
    let identifier = match &global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => GlobalContractCodeIdentifier::CodeHash(*hash),
        GlobalContractIdentifier::AccountId(account_id) => {
            GlobalContractCodeIdentifier::AccountId(account_id.clone())
        }
    };

    let is_nonce_fresh = check_and_update_nonce(global_contract_data, &identifier, state_update)?;
    if !is_nonce_fresh {
        return Ok(0);
    }

    let config = apply_state.config.wasm_config.clone();
    let trie_key = TrieKey::GlobalContractCode { identifier };
    let code_len = global_contract_data.code().len() as u64;
    state_update.set(trie_key, global_contract_data.code().to_vec());
    state_update.commit(StateChangeCause::ReceiptProcessing { receipt_hash: receipt.get_hash() });
    let code_hash = match global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => Some(*hash),
        GlobalContractIdentifier::AccountId(_) => None,
    };
    precompile_contract_with_warming(
        &ContractCode::new(global_contract_data.code().to_vec(), code_hash),
        config,
        apply_state.next_wasm_config.clone(),
        apply_state.cache.as_deref(),
    );
    near_vm_runner::report_metrics(apply_state.shard_id, "global_contract");
    let fees = &apply_state.config.fees;
    let per_byte_total = fees
        .deploy_global_contract_execution_per_byte
        .checked_mul(code_len)
        .ok_or(IntegerOverflowError)?;
    let compute = fees
        .deploy_global_contract_execution_base
        .checked_add(per_byte_total)
        .ok_or(IntegerOverflowError)?;
    Ok(compute)
}
```

**File:** docs/RuntimeSpec/Actions.md (L440-456)
```markdown
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

**Outcome**:

- First, the provided code is made available as global contract on the current shard.
- The same code propagates globally, shard by shard.
- Eventually, all accounts on all shards can reference the submitted code by the corresponding global contract identifier.
```

**File:** test-loop-tests/src/tests/global_contracts.rs (L71-105)
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
```
