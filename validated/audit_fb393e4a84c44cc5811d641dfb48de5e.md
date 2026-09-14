### Title
`AccountId`-mode global contracts let the owner silently swap executed WASM code between a victim's review/`UseGlobalContract` decision and the actual `FunctionCall` execution - ([File: runtime/runtime/src/global_contracts.rs])

### Summary
NEAR's global-contract feature supports two reference modes. `GlobalContractDeployMode::CodeHash` pins code content-addressably and is immutable, but `GlobalContractDeployMode::AccountId` explicitly lets the deploying account "update the contract for all its users" at will [1](#0-0) . Any account that adopts such a contract via `UseGlobalContractAction { contract_identifier: GlobalContractIdentifier::AccountId(...) }` is bound only to the publisher's account id, not to any specific code hash, and every subsequent `FunctionCall` resolves the *currently stored* code for that account id at execution time [2](#0-1) . This is structurally the same trust failure as the Maple Finance report: code is referenced by a mutable identity rather than by a commitment to specific bytecode, so the code a victim reviewed/expected can be different from the code that actually executes by the time their transaction lands.

### Finding Description
When a user (or a contract acting on a user's behalf) issues `UseGlobalContract` with an `AccountId` identifier, the runtime just checks that *some* code currently exists under that publisher account id and stores `AccountContract::GlobalByAccount(id)` on the receiver — no code hash is captured or pinned [3](#0-2) .

From that point on, every `FunctionCall` receipt sent to the receiver resolves its contract code fresh, at apply time, via `RuntimeContractIdentifier::resolve`, which for a `GlobalByAccount` reference simply looks up whatever code is currently stored under `TrieKey::GlobalContractCode { identifier: AccountId(...) }` [4](#0-3) . There is no re-validation that the code matches what existed when the victim decided to trust that publisher or signed a transaction expecting particular behavior.

The publisher can redeploy new code under the same `AccountId` identifier at any time via another `DeployGlobalContractAction` with `deploy_mode: AccountId`; `initiate_distribution` derives the same `GlobalContractIdentifier::AccountId(account_id)` and simply overwrites the stored code for later-processed distribution receipts, gated only by a strictly-increasing nonce (not by content) [5](#0-4) , [6](#0-5) . Tests explicitly confirm that redeployment under `AccountId` mode changes the code executed by all downstream users that reference that account id, even after they already called `UseGlobalContract` [7](#0-6) .

This mirrors the Maple `_acceptNewTerms` delegatecall issue exactly: the victim's decision (accepting terms / calling `UseGlobalContract`) is made against one version of the code, but execution (delegatecall / `FunctionCall`) can run under a different version that the counterparty swapped in between — except on NEAR this requires no selfdestruct/CREATE2 trick at all; overwriting the `AccountId`-keyed global contract is a first-class, permitted action available to the publisher at any time, including immediately before a chunk containing the victim's pending `FunctionCall` or `UseGlobalContract` transaction is produced.

### Impact Explanation
A borrower/counterparty analog in NEAR is any account that publishes a global contract in `AccountId` mode and invites other accounts/users to `UseGlobalContract` against it (e.g., a shared library, wallet-recovery contract, or DeFi logic contract advertised as audited). A user who reviews the currently deployed code, decides it is safe, and then submits `UseGlobalContract` (or a `FunctionCall` against an account already using it) can have the publisher redeploy materially different — potentially malicious — logic under the same account id before the user's transaction is included. Because the resolved code is fetched fresh at `FunctionCall` execution and is not bound to any hash the user committed to, the user's account ends up executing arbitrary attacker-chosen WASM with the account's own credentials and state access, which can result in unauthorized transfers, key manipulation, or manipulation of account storage the user did not consent to — i.e., concrete unauthorized value movement analogous to the Maple delegatecall exploit.

### Likelihood Explanation
This requires only ordinary, permitted transactions from an unprivileged account that controls an `AccountId`-mode global contract: no selfdestruct/CREATE2, no malicious validator, and no network-layer manipulation is needed (unlike the original Solidity report, which needed the extra CREATE2 recreation trick). The publisher simply needs to time a legitimate `DeployGlobalContractAction` (same `AccountId` mode, higher nonce) to land in a chunk before/around the victim's `UseGlobalContract`/`FunctionCall` transaction — which is directly achievable by a transaction-submitting account without any privileged role. This is comparable in difficulty to (and arguably easier than) the original Medium-severity Maple finding.

### Recommendation
- Allow (and encourage) users to pin `UseGlobalContract` to a specific code hash even when the contract is published in `AccountId` mode, e.g., by letting `UseGlobalContractAction` optionally carry an expected code hash that must match the account-id-resolved code at execution time, returning an action error (analogous to `GlobalContractDoesNotExist`) on mismatch.
- Consider requiring `FunctionCall` execution against `GlobalByAccount` contracts to verify the code hash against a hash captured at the most recent `UseGlobalContract` action for that account, rather than resolving whatever is currently stored, closing the "swap after review" window entirely for accounts that want tamper-evidence.
- At minimum, strengthen the "the owner can update the contract for all its users" documentation with an explicit warning about this front-running/rug-pull risk so integrators default to `CodeHash` mode for any contract handling value.

### Proof of Concept
1. Publisher account `pub.near` deploys a benign contract under `GlobalContractDeployMode::AccountId` (`DeployGlobalContractAction`) [8](#0-7) .
2. Victim account `alice.near` inspects the code, trusts it, and submits `UseGlobalContract { contract_identifier: AccountId(pub.near) }`, binding her account to `AccountContract::GlobalByAccount(pub.near)` [3](#0-2) .
3. Before Alice's subsequent `FunctionCall` transaction (or even before her `UseGlobalContract` transaction) is included in a chunk, `pub.near` submits another `DeployGlobalContractAction` with `deploy_mode: AccountId` containing malicious code and a higher nonce; `initiate_distribution`/`apply_distribution_current_shard` overwrite the code stored for `GlobalContractIdentifier::AccountId(pub.near)` [9](#0-8) .
4. Alice's `FunctionCall` executes; `RuntimeContractIdentifier::resolve` fetches the now-malicious code for `AccountContract::GlobalByAccount(pub.near)` and it runs with Alice's account context [2](#0-1) , [10](#0-9) .
5. `test_global_contract_update` demonstrates this exact update-after-use mechanic (functionally identical sequence, only with a benign second contract in the test) [7](#0-6) .

### Citations

**File:** core/primitives/src/action/mod.rs (L140-143)
```rust
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
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

**File:** runtime/runtime/src/global_contracts.rs (L143-225)
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

**File:** runtime/runtime/src/lib.rs (L684-700)
```rust
            Action::FunctionCall(function_call) => {
                metrics::ACTION_CALLED_COUNT.function_call.inc();
                let account = account.as_mut().expect(EXPECT_ACCOUNT_EXISTS);
                let account_contract = account.contract().into_owned();
                let contract_id = RuntimeContractIdentifier::resolve(
                    account_id,
                    account_contract,
                    &state_update,
                    &epoch_info_provider.chain_id(),
                    AccessOptions::DEFAULT,
                )?;
                let contract = preparation_pipeline.get_contract(
                    receipt,
                    contract_id.clone(),
                    action_index,
                    None,
                );
```
