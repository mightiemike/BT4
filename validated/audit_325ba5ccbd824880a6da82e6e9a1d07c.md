This confirms the mechanism: `AccountContract::GlobalByAccount(id)` is a live pointer, resolved dynamically at every `FunctionCall`, not a snapshot taken at `UseGlobalContract` time [1](#0-0) . Any subsequent `DeployGlobalContract` with `GlobalContractDeployMode::AccountId` by the owner immediately changes what code runs for every account that referenced it [2](#0-1) .

### Title
Front-runnable code substitution via `AccountId`-mode global contracts enables theft of in-flight deposits - (File: `runtime/runtime/src/global_contracts.rs`)

### Summary
NEAR's global contracts feature lets an account deploy WASM code addressed by an owner-controlled `AccountId` rather than an immutable code hash [2](#0-1) . Any account can adopt that code via `UseGlobalContract`, which stores only a pointer (`AccountContract::GlobalByAccount(id)`), not a snapshot of the code [3](#0-2) . Every `FunctionCall` receipt to that account resolves the *current* code for that owner id at execution time via `RuntimeContractIdentifier::resolve` [4](#0-3) . This is structurally identical to the reported Teller bug: an unrelated party (here, the global-contract owner) can inject/alter shared, code-level state that a third party's already-signed, in-flight transaction depends on, with no consent from either the account under attack or the counterparty sending funds to it.

### Finding Description
Consider account `V` (victim) that has run `UseGlobalContract(AccountId: M)`, adopting malicious-owner `M`'s currently-benign contract code (`GlobalContractDeployMode::AccountId`) [5](#0-4) . A third party `P` observes `V`'s advertised contract behavior (e.g., a deposit-and-refund or escrow method) and submits a `FunctionCall` transaction with an attached deposit to `V`, expecting the known logic to run.

Before `P`'s transaction is included, `M` submits `DeployGlobalContractAction` again under `AccountId` mode with new, malicious code. Because `V`'s account only stores `GlobalByAccount(M)` — a pointer, not a code snapshot — `P`'s function call, once it lands, executes whatever code `M` has deployed at that moment [1](#0-0) . `M` can time this redeploy to land in the same or an earlier block than `P`'s transaction (chunk producers/validators can order transactions within their control, and any account can simply race the redeploy against the mempool), analogous to the MEV front-run described in the report. There is no mechanism for `V` or `P` to pin a specific code version once `UseGlobalContract(AccountId)` has been chosen, and no re-consent step is required for the owner's update to take effect for already-adopted accounts [6](#0-5) .

The `CodeHash` deploy mode is immune, since it references immutable content-addressed code [7](#0-6) , but `AccountId` mode was explicitly designed to let "the owner update the contract for all its users" [8](#0-7)  with no opt-out, versioning pin, or timelock visible in the action/runtime code.

### Impact Explanation
If a malicious (or later-compromised) owner controls a widely-adopted `AccountId`-mode global contract, they can redeploy malicious code to intercept in-flight deposits/function calls sent to any account that references it, redirecting attached NEAR deposits or manipulating logic to the owner's benefit — concrete unauthorized value movement. It can also be used purely to grief: swap in code that always panics or behaves incompatibly with the caller's expectations, causing legitimate transactions targeting `V` to fail non-deterministically depending on redeploy timing.

### Likelihood Explanation
Likelihood is moderate: it requires (1) some victim account(s) to have adopted an `AccountId`-mode global contract from an account whose key security later fails or who turns malicious, and (2) a counterparty to send a transaction depending on the currently-deployed logic. Given `AccountId` mode is explicitly advertised as an update mechanism (e.g., used for ZBA/shared-library patterns in tests), real usage is plausible, and the redeploy/front-run only requires submitting one ordinary `DeployGlobalContractAction` transaction — no special privilege beyond owning the global-contract account.

### Recommendation
For `AccountId`-mode global contracts, consider requiring adopting accounts to explicitly opt into updates (e.g., a "pin" flag defaulting to pinning the code hash observed at `UseGlobalContract` time, with an explicit follow-up action to accept a new version), or exposing a way to detect/replay-protect against code changes landing between a caller's transaction submission and inclusion — e.g., surfacing the resolved code hash in the receipt so wallets/relays can abort if it changed. At minimum, this design's implicit trust assumption (an adopting account fully trusts a code owner never to publish malicious updates, and third parties transacting with that account trust it transitively) should be documented as a security consideration for integrators.

### Proof of Concept
1. `M` calls `DeployGlobalContractAction { code: benign_code, deploy_mode: AccountId }` [9](#0-8) .
2. `V` calls `UseGlobalContractAction { contract_identifier: AccountId(M) }`; `V`'s account is now `AccountContract::GlobalByAccount(M)` [10](#0-9) .
3. `P` broadcasts a `FunctionCall` to `V` with an attached deposit, relying on `benign_code`'s documented behavior.
4. `M` observes `P`'s pending transaction and submits `DeployGlobalContractAction { code: malicious_code, deploy_mode: AccountId }`, which is processed before or in the same block as `P`'s call (test coverage confirms redeploys immediately take effect for all referencing accounts without any additional action from them) [11](#0-10) .
5. `P`'s transaction executes against `malicious_code`, which can, e.g., forward `P`'s deposit to `M` instead of performing the expected escrow logic — value is moved without `P`'s or `V`'s consent to that specific code path.

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

**File:** protocol-model/spec/runtime-execution.md (L88-88)
```markdown
| `FunctionCall` | `action_function_call` `runtime/runtime/src/function_call.rs:31` | Resolves the contract (`RuntimeContractIdentifier::resolve`), fetches the prepared contract from the pipeline, invokes the [contract VM](contract-vm.md); records burnt/used gas, logs, new receipts. |
```

**File:** docs/RuntimeSpec/Actions.md (L440-449)
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
