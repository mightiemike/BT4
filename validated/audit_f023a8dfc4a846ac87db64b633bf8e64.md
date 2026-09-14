Based on the codebase, there is a clear analog to the "Contract Owner Possesses Too Many Privileges" bug class in NEAR's global contract sharing feature.

### Title
`GlobalContractDeployMode::AccountId` Lets a Contract Owner Silently Rug-Pull Every Downstream Subscriber Account - (File: `runtime/runtime/src/global_contracts.rs`)

### Summary
NEAR's global contract feature lets an account deploy WASM code once and have unrelated accounts "subscribe" to it by reference, via `UseGlobalContractAction`. When the code is deployed with `GlobalContractDeployMode::AccountId`, subscribing accounts bind to a mutable pointer keyed on the deployer's `AccountId` rather than an immutable hash. The deployer can redeploy new code under that same identifier at any time, and every subscriber's account immediately and irrevocably runs the new code with full authority over that subscriber's own NEAR balance and storage — with no re-approval, timelock, or opt-out for subscribers. This is architecturally identical to the ExecutionDelegate/`approveContract` rug-pull pattern: users delegate trust once, and the trusted party can unilaterally redirect that trust to arbitrary new logic that moves user funds.

### Finding Description
`GlobalContractDeployMode` explicitly documents this trust model: `CodeHash` "effectively makes the contract immutable," while `AccountId` "allows the owner to update the contract for all its users" [1](#0-0) .

A subscribing account binds via `use_global_contract`, which sets `AccountContract::GlobalByAccount(id)` on the subscriber's own account with no versioning, snapshot, or consent gate beyond the initial call [2](#0-1) .

The deployer can call `action_deploy_global_contract` again at any later point, unrestricted, which re-triggers distribution and overwrites the trie entry for that `AccountId` identifier: [3](#0-2)  and [4](#0-3) . The only freshness control is a strictly-increasing nonce used for ordering distribution receipts, not an authorization or consent check on behalf of subscribers [5](#0-4) .

When any subscriber account is subsequently called, `RuntimeContractIdentifier::resolve` transparently maps the subscriber's stored `AccountContract::GlobalByAccount(id)` to whatever code currently lives at that identifier — the subscriber has no way to pin to the version they originally reviewed and approved [6](#0-5) . Because contract code executing under an account has native authority to move that account's own NEAR balance and manipulate its own persistent storage via promises (this is a base capability of any deployed contract, not something access keys gate), a malicious or phished/compromised deployer can push new code that silently drains every subscriber account the moment it's invoked.

### Impact Explanation
Any account that adopts a shared global contract (motivated by the documented storage-cost savings) is implicitly granting the deployer permanent, revocation-free proxy-admin rights over its own balance and state. If the deployer's key is compromised or the deployer turns malicious, they can redeploy code (e.g., a method issuing `Promise::transfer` of the full account balance to an attacker-controlled account) that instantly executes with full authority inside every subscriber account on their next invocation — concrete unauthorized value movement across an arbitrary number of victim accounts, with no user-side revocation window analogous to `revokeApproval()` in the reported ExecutionDelegate issue.

### Likelihood Explanation
Medium. The attack requires accounts to have opted into `UseGlobalContractAction{AccountId(...)}` for a given deployer — which is exactly the intended use case the feature markets (shared/cheaper contract code, e.g. shared NEP-141 or wallet-style contracts). Both the initial deploy, subscription (`UseGlobalContractAction`), and the malicious redeploy are ordinary unprivileged transactions reachable by any signer; no validator, node-operator, or protocol-level privilege is required — only the social/economic trust relationship that the design itself already documents as owner-controlled.

### Recommendation
Do not allow a mutable, unversioned trust pointer to carry latent authority over subscriber funds. Options: (1) require subscribers to pin to an explicit code hash/version and re-confirm via a new `UseGlobalContractAction` after each redeploy rather than silently inheriting updates; (2) add a mandatory timelock/delay on `AccountId`-mode redeployments so subscribers have a window to detach (switch back to `CodeHash` mode or a different contract) before new code becomes active; (3) surface redeployment events prominently enough (e.g., queryable "pending update" state) that automated tooling/wallets can warn or auto-revoke on behalf of users.

### Proof of Concept
1. Attacker deploys benign global contract code with `DeployGlobalContractAction{deploy_mode: GlobalContractDeployMode::AccountId}` [3](#0-2) .
2. Victim accounts call `UseGlobalContractAction{contract_identifier: GlobalContractIdentifier::AccountId(attacker)}`, permanently binding `AccountContract::GlobalByAccount(attacker)` on their own accounts [2](#0-1) .
3. Attacker later submits another `DeployGlobalContractAction` under the same `AccountId` mode with malicious code (e.g. a method that calls `Promise::new(attacker).transfer(env::account_balance())`); this overwrites the shared trie entry via `initiate_distribution`/`apply_distribution_current_shard` with no consent from subscribers [7](#0-6) .
4. The next time any victim account's global-contract method is invoked, `RuntimeContractIdentifier::resolve` fetches the newly-deployed malicious code for that `AccountId` identifier and executes it with the victim account's full balance/storage authority, draining the funds [6](#0-5) .

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

**File:** runtime/runtime/src/global_contracts.rs (L76-108)
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

**File:** runtime/runtime/src/global_contracts.rs (L173-246)
```rust
/// Increments the nonce for the given global contract identifier and writes
/// it to state immediately.
fn increment_nonce(
    state_update: &mut TrieUpdate,
    id: &GlobalContractIdentifier,
) -> Result<u64, RuntimeError> {
    let identifier: GlobalContractCodeIdentifier = id.clone().into();

    let nonce_key = TrieKey::GlobalContractNonce { identifier };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;

    let new_nonce = stored_nonce.checked_add(1).ok_or_else(|| {
        RuntimeError::UnexpectedIntegerOverflow("increment_global_contract_nonce".into())
    })?;
    set_nonce(state_update, nonce_key, new_nonce);
    Ok(new_nonce)
}

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

**File:** runtime/runtime/src/contract_code.rs (L36-73)
```rust
    pub(crate) fn resolve(
        account_id: &AccountId,
        account_contract: AccountContract,
        state_update: &TrieUpdate,
        chain_id: &str,
        access: AccessOptions,
    ) -> Result<Self, StorageError> {
        let local_hash = match GlobalContractIdentifier::try_from(account_contract) {
            Ok(gci) => {
                let code_hash = gci.clone().hash(state_update, access)?;
                return Ok(RuntimeContractIdentifier::Global { code_hash, identifier: gci });
            }
            Err(ContractIsLocalError::NotDeployed) => return Ok(RuntimeContractIdentifier::None),
            Err(ContractIsLocalError::Deployed(local_hash)) => local_hash,
        };

        if account_id.get_account_type() == AccountType::EthImplicitAccount {
            // Accounts that look like eth implicit accounts and have existed prior to the
            // eth-implicit accounts protocol change (these accounts are discussed in the
            // description of #11606) may have something else deployed to them. Only return
            // something here if the accounts have a wallet contract hash. Otherwise use the
            // regular path to grab the deployed contract.
            if LegacyEthWallet::resolve(local_hash).is_some() {
                // ETH implicit wallet accounts use global contracts, including
                // those created in old protocol versions.
                let global_hash = eth_wallet_global_contract_hash(chain_id);
                return Ok(RuntimeContractIdentifier::Global {
                    code_hash: global_hash,
                    identifier: GlobalContractIdentifier::CodeHash(global_hash),
                });
            }
        }

        Ok(RuntimeContractIdentifier::AccountLocal {
            code_hash: local_hash,
            account_id: account_id.clone(),
        })
    }
```
