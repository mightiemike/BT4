## Analysis Result

The nearcore analog to this "owner can rug-pull via mutable implementation" bug class is the `GlobalContractDeployMode::AccountId` deployment mode for global contracts, combined with `UseGlobalContract`/`AccountContract::GlobalByAccount`. This mirrors the report's exact pattern: a single account ("owner") controls code that executes with another account's full balance/storage authority, and that code can be silently swapped at any time.

### Title
Mutable global contracts referenced by `AccountId` let the deploying account rug-pull any account that adopted them - ([File: runtime/runtime/src/global_contracts.rs])

### Summary
`DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId` publishes WASM code addressed by the deploying account's id rather than its hash. Any other account can point at that code via `UseGlobalContractAction { contract_identifier: GlobalContractIdentifier::AccountId(owner) }`, after which `FunctionCall` actions on the referencing account execute that shared code with the referencing account's own balance and storage context. Because the code is keyed by account id, not by hash, the original deployer can redeploy entirely different (malicious) code under the same identifier at any later time, and every account that adopted it via `UseGlobalContract` will transparently start executing the new code the next time it is invoked - with full authority to drain the referencing account's NEAR balance.

### Finding Description
- `GlobalContractDeployMode::AccountId` is explicitly documented as mutable: "Contract is deployed under the owner account id... This allows the owner to update the contract for all its users." [1](#0-0) 
- `action_deploy_global_contract` lets the deployer redeploy code under `GlobalContractIdentifier::AccountId(account_id)` with no restriction preventing overwriting a previously published version. [2](#0-1) 
- `initiate_distribution`/`apply_distribution_current_shard` only guard against *stale* (out-of-order) redeploys via a monotonically increasing nonce (`check_and_update_nonce` allows `incoming_nonce >= stored_nonce`); it does not prevent a *newer* nonce from replacing the code entirely. [3](#0-2) [4](#0-3) 
- `use_global_contract` sets `AccountContract::GlobalByAccount(id)` on the referencing account, i.e., it stores a *pointer to the owner account*, not a hash of the code that existed at adoption time. [5](#0-4) 
- `FunctionCall` on the referencing account executes whatever code is currently registered under that pointer, with the referencing account as `current_account_id`, giving the (now swapped) code full authority over that account's balance/storage (per the runtime execution model, code resolution happens via `AccountContract`). [6](#0-5) 
- This is directly analogous to the reported bug class: an `Ownable`-style privileged actor can change "implementation" code that executes on behalf of victims' funds, matching the report's rug-pull pattern.

### Impact Explanation
Any account that references a global contract via `GlobalContractIdentifier::AccountId(owner)` implicitly grants the `owner` account continuing, unilateral control over the code that runs with its own balance and storage. If the `owner` deploys a benign contract to build trust/adoption, and later redeploys malicious code (e.g., a contract that immediately issues a `Transfer` promise draining the calling account's balance to an attacker-controlled account), every account still referencing that identifier is exposed to unauthorized value movement the next time their account executes a `FunctionCall`. This is a concrete unauthorized value-movement primitive reachable purely through ordinary, unprivileged transactions (`DeployGlobalContract` + `UseGlobalContract` + later `DeployGlobalContract` again).

### Likelihood Explanation
Reachable entirely through standard, permission-less transactions from any account: (1) deploy a global contract in `AccountId` mode, (2) have/entice other accounts to call `UseGlobalContract` pointing at that account id, (3) redeploy different code under the same account id at will. No validator, network, or privileged-role assumption is required — only a victim account choosing to adopt the shared contract, which is the intended usage pattern for this feature (e.g., to save storage costs across many accounts running the same logic).

### Recommendation
- Treat `GlobalContractIdentifier::AccountId` references as inherently mutable-trust and clearly document/warn at the API and RPC-schema level that adopting such a contract grants the publishing account perpetual code-execution authority over the referencing account's funds.
- Consider adding an explicit "pin to code hash" or "opt out of future updates" mechanism so a referencing account can lock in a specific code version (effectively converting a `GlobalByAccount` reference to a `Global` hash-pinned reference) without needing to redeploy locally.
- Consider requiring an explicit re-confirmation (e.g., a fresh `UseGlobalContract` action) after a redeploy under the same account id before the new code is trusted by previously-adopting accounts, rather than silent, automatic adoption of new code by nonce alone.

### Proof of Concept
1. Attacker account `attacker.near` deploys a benign global contract with `DeployGlobalContractAction { deploy_mode: GlobalContractDeployMode::AccountId, code: benign_wasm }` (per `action_deploy_global_contract` in `runtime/runtime/src/global_contracts.rs:25`).
2. Victim account `victim.near` calls `UseGlobalContractAction { contract_identifier: GlobalContractIdentifier::AccountId("attacker.near") }`, which sets `victim.near`'s `AccountContract::GlobalByAccount("attacker.near")` (`runtime/runtime/src/global_contracts.rs:76-109`), and successfully calls the benign contract's methods to build trust.
3. `attacker.near` later submits another `DeployGlobalContractAction` with the same `AccountId` mode but malicious code that, when called, issues a `Transfer` promise to attacker's account for the caller's entire balance. Because nonce freshness only requires `incoming_nonce >= stored_nonce` (`runtime/runtime/src/global_contracts.rs:248-269`), this redeploy succeeds and overwrites the code referenced by `victim.near`.
4. The next `FunctionCall` receipt sent to `victim.near` (e.g., a routine method invocation `victim.near` regularly receives) now executes the malicious code with `victim.near`'s account context, draining its balance to `attacker.near` — with `victim.near` never having re-authorized anything after step 2.

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

**File:** runtime/runtime/src/global_contracts.rs (L25-63)
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

**File:** runtime/runtime/src/global_contracts.rs (L248-269)
```rust
// Checks if the incoming nonce is fresh and updates the stored nonce. Returns
// true if the nonce is fresh, false if it's stale. The nonce is set
// immediately and the freshness check allows the same nonce (>=).
fn check_and_update_nonce(
    global_contract_data: &GlobalContractDistributionReceipt,
    identifier: &GlobalContractCodeIdentifier,
    state_update: &mut TrieUpdate,
) -> Result<bool, RuntimeError> {
    let nonce_key = TrieKey::GlobalContractNonce { identifier: identifier.clone() };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;
    let incoming_nonce = global_contract_data.nonce();

    // Allow the same nonce since the nonce is updated immediately when
    // initiating distribution to prevent multiple distributions with the same
    // nonce from being initiated.
    if incoming_nonce < stored_nonce {
        return Ok(false);
    }

    set_nonce(state_update, nonce_key, incoming_nonce);
    Ok(true)
}
```

**File:** protocol-model/spec/accounts-keys.md (L35-35)
```markdown
A receiver account's `AccountContract` tells the runtime where to fetch code for a `FunctionCall`. `Local(hash)` reads code stored under this account; `Global(hash)` and `GlobalByAccount(id)` reference a single shared deployment so many accounts can run the same code without each paying to store it (global contracts; the deploy/use transitions and `GlobalContractIdentifier`, `core/primitives-core/src/global_contract.rs:25`, are in [runtime-execution](runtime-execution.md)). ETH-implicit accounts are created already pointing at a `Global` wallet contract (`actions.rs:249`). Only `Local` code counts toward this account's own contract storage; `Global` costs a fixed 32-byte identifier and `GlobalByAccount` costs the publisher id's length (`account.rs:126`).
```
