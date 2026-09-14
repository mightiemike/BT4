### Title
Owner of an `AccountId`-mode global contract can unilaterally, instantly, and unboundedly rewrite the code executed by every account referencing it, enabling a rug-pull/front-run analogous to the FERC1155 controller-set-royalties exploit - (File: `runtime/runtime/src/global_contracts.rs`)

### Summary
The FERC1155 finding is a class of bug where a single privileged party (the vault owner acting as `controller`) can arbitrarily and instantly rewrite a parameter (royalty %) that other, unrelated parties rely on, with no cap and no timelock, letting the owner front-run a specific transaction to steal value. NEAR's global-contract `AccountId` deploy mode reproduces the same structural weakness: an account that references a global contract by `GlobalContractIdentifier::AccountId(owner)` does not pin to a specific version of the code — it always executes whatever code `owner` most recently deployed, and `owner` can redeploy at any time, unboundedly, with no timelock and no consent step from the referencing accounts.

### Finding Description
`UseGlobalContractAction` lets an account point its `AccountContract` at `GlobalContractIdentifier::AccountId(owner)`, stored as `AccountContract::GlobalByAccount(owner)`: [1](#0-0) 

This is a *reference*, not a snapshot: the actual WASM bytes are fetched from `TrieKey::GlobalContractCode { identifier }` at call time, and that trie entry is overwritten every time `owner` calls `DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId`: [2](#0-1) [3](#0-2) 

The only "freshness" mechanism is a monotonically incrementing nonce that prevents *stale* distribution receipts from overwriting a *newer* deploy across shards — it does not throttle, cap, or delay how often or how drastically `owner` can change the code: [4](#0-3) 

The protocol's own schema documentation acknowledges this is intended but explicitly names the risk surface: *"This allows the owner to update the contract for all its users."* [5](#0-4) 

The integration test `test_global_contract_update` demonstrates the mechanic end-to-end: accounts `use_global_contract` against `owner`'s `AccountId` identifier, and a later `owner` redeploy under the same identifier immediately changes the behavior observed by every account that already opted in, with zero additional action from those accounts: [6](#0-5) 

This exactly parallels the FERC1155 pattern: `VaultRegistry`/`FERC1155.setRoyalties` let a `controller` (analogous to the global-contract `owner`) change a live parameter (royalty %) that other parties (fToken holders/buyers, analogous to accounts that called `UseGlobalContractAction`) depend on, with no cap and no timelock, enabling a front-run "change it right before the victim's transaction executes, then revert it" attack.

### Impact Explanation
Because the referencing account's `FunctionCall` receipts execute whatever code `owner` has currently deployed under `GlobalByAccount(owner)`, and that code runs with the referencing account as `predecessor_id`/actor for its own promises (balance transfers, cross-contract calls), a malicious or compromised `owner` can:
1. Deploy innocuous code and let many accounts `UseGlobalContractAction` against it (building trust/adoption), then
2. Watch the mempool/chunk for a lucrative pending transaction directed at one of the referencing accounts, and
3. Redeploy malicious code (e.g., one that siphons the caller's attached deposit, redirects a payment/transfer promise to an attacker-controlled account, or behaves differently only for the targeted call) immediately before that transaction is included, then optionally revert back afterward.

This is a concrete, transaction-triggered path to unauthorized value movement (misdirected transfers/promises) affecting any account that opted into the `AccountId` global-contract reference model, without those accounts taking any further action.

### Likelihood Explanation
This requires (a) at least one account to have opted in via `UseGlobalContractAction` with `GlobalContractIdentifier::AccountId(owner)`, and (b) `owner` to be malicious or compromised — structurally identical to the original finding's requirement of "a malicious vault owner." Given that `AccountId` mode is explicitly marketed as mutable/upgradeable (vs. the immutable `CodeHash` mode), and no protocol-level cap/timelock exists on redeployment frequency or magnitude of change, the likelihood is comparable to the original Medium-severity finding: it requires a malicious privileged party, but no other protection prevents exploitation once that party exists.

### Recommendation
For `GlobalContractDeployMode::AccountId`, consider adding an opt-in timelock/delay between `DeployGlobalContractAction` and the new code becoming effective for already-referencing accounts, and/or let referencing accounts pin to a specific deployment nonce/version via `UseGlobalContractAction` rather than always tracking the latest code, requiring an explicit re-`Use` action to accept an upgrade. This mirrors the original report's mitigation (cap + timelock) applied to the nearcore equivalent of "controller-settable parameter."

### Proof of Concept
Using `test_global_contract_update` as the base flow: [6](#0-5) 

1. `owner` deploys an initial global contract via `GlobalContractDeployMode::AccountId` (benign code).
2. `victim` account calls `UseGlobalContractAction { contract_identifier: GlobalContractIdentifier::AccountId(owner) }`, becoming `AccountContract::GlobalByAccount(owner)`.
3. `victim` publicizes/relies on the current benign behavior (e.g., a fixed-fee payment-forwarding method), and third parties start sending it value-bearing calls.
4. `owner`, observing a large pending call to `victim` in the chunk/mempool, submits `DeployGlobalContractAction` (same `AccountId` mode) with malicious code that redirects the attached deposit/promise to `owner`'s own account for that specific method signature.
5. Because `action_deploy_global_contract` → `initiate_distribution` → `apply_distribution_current_shard` overwrite the `TrieKey::GlobalContractCode` entry with no cap or delay, and `victim`'s account still resolves to `GlobalByAccount(owner)`, the very next `FunctionCall` receipt against `victim` executes the malicious code and can move `victim`'s balance/promise ledger contrary to what the caller expected — an unauthorized value movement triggered entirely by `owner`'s own transaction, mirroring Alice's royalty front-run in the original report.

### Citations

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

**File:** runtime/runtime/src/global_contracts.rs (L191-245)
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

**File:** core/primitives/src/action/mod.rs (L140-143)
```rust
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
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
