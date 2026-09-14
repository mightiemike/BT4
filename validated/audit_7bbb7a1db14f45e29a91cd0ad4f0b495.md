## Analog Found [1](#0-0) 

### Title
Global contract `AccountId` deploy mode grants the deploying owner unilateral, un-timelocked control to rewrite code executed on behalf of every account that referenced it - (File: `runtime/runtime/src/global_contracts.rs`, `core/primitives/src/action/mod.rs`)

### Summary
`DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId` lets an account publish code that other accounts adopt via `UseGlobalContract`/`UseGlobalContractByAccountId`, or that deterministic accounts (NEP-616) bind to permanently through `GlobalContractIdentifier::AccountId(owner)`. The protocol explicitly documents that this mode "allows the owner to update the contract for all its users" — the owner can redeploy new code under the same `AccountId` identifier at any time, with no timelock, no consent step from the referencing accounts, and no way for a referencing account to detach or pin to the version it originally trusted. This is structurally the same trust-abuse pattern as the reported `VestingEscrowFactory` issue: users delegate control (approval / contract reference) expecting a specific, limited use, but the privileged party (`owner`/global-contract publisher) retains standing authority to redefine what that delegated trust actually executes, including logic that runs with the referencing account's own balance and identity.

### Finding Description
- `GlobalContractDeployMode::AccountId` is documented at [1](#0-0)  as allowing "the owner to update the contract for all its users."
- `action_deploy_global_contract` in [2](#0-1)  performs no check preventing redeployment under an `AccountId` identifier that other accounts already reference — any subsequent `DeployGlobalContract` by the same owner account simply re-triggers `initiate_distribution`.
- `initiate_distribution` in [3](#0-2)  keys the distribution solely by `GlobalContractIdentifier::AccountId(account_id)`, and relies only on a monotonically increasing nonce to guarantee the latest deploy wins — there is no versioning, pinning, or opt-out mechanism for accounts that already called `UseGlobalContract` against that identifier.
- Test coverage confirms the update propagates to all consuming accounts transparently: `test_global_contract_update` shows a trivial contract replaced by `rs_contract` under the same `AccountId`, and every account that previously called `use_global_contract` against that identifier now executes the new code without re-consenting [4](#0-3) .
- Deterministic accounts (NEP-616) can permanently bind their code reference to `GlobalContractIdentifier::AccountId(owner)` as part of their `DeterministicAccountStateInit`, per [5](#0-4) . Once funded, any code the owner later pushes under that `AccountId` runs with the full authority (balance, predecessor identity, promise-creation rights) of every deterministic account bound to it — the owner never needs further authorization from those accounts' controllers.

This mirrors the reported bug class exactly: a party that is trusted for a narrow purpose (a factory holding a token approval; here, a global-contract publisher whose code is referenced by other accounts) retains unrestricted, standing authority that can be exercised unilaterally and asynchronously to redirect value/behavior controlled by the trusting party.

### Impact Explanation
If a global-contract `AccountId` owner's key is compromised, or the owner is malicious/rug-pulls after gaining adoption, every account that referenced that identifier via `UseGlobalContract` (or was created as a deterministic account bound to it) is exposed: the owner can push new code that executes with the referencing account's own balance, spawning `Transfer`/`FunctionCall` promises against `predecessor_account_id() == <referencing account>` — a path to unauthorized value movement across every dependent account, not just the deployer's own. This is unlike `CodeHash` mode (immutable, safe to trust indefinitely) — `AccountId` mode's entire design point is mutability, but nothing bounds or discloses that risk to a referencing account at the point of `UseGlobalContract`.

### Likelihood Explanation
Reachable by an ordinary contract deployer via two standard transactions: (1) `DeployGlobalContract` with `AccountId` mode to gain adopters, (2) a later `DeployGlobalContract` under the same identifier with malicious code, once enough accounts have called `UseGlobalContract` against it. No validator, network, or operator privilege is required — only ordinary account-owner signing rights, matching the "contract deployer" actor class this analysis is scoped to.

### Recommendation
Since this reflects intended, explicitly documented protocol behavior (as acknowledged in the code comments themselves), the practical mitigations are protocol/tooling-level: warn or require explicit acknowledgment at `UseGlobalContract` time that the referenced code is mutable and owner-controlled; consider optional pinning to a specific nonce/version of an `AccountId`-mode global contract; and/or provide a way for a referencing account to detach from future updates.

### Proof of Concept
1. Owner account deploys a benign global contract with `GlobalContractDeployMode::AccountId` (see `test_global_contract_update` setup) [6](#0-5) .
2. Multiple user accounts call `UseGlobalContractAction { contract_identifier: GlobalContractIdentifier::AccountId(owner) }`, adopting the code.
3. Owner submits a second `DeployGlobalContractAction` under the same `AccountId` mode with malicious code (e.g., one that on invocation creates a `Transfer` promise draining the calling account's balance to an attacker address).
4. Any of the referencing accounts calling a method on their (unchanged, still-pointing) contract reference now executes the attacker's new code with their own balance and predecessor authority — no additional consent was solicited, matching the same "owner had excessive/standing access to funds a user implicitly trusted to a third party" bug class as the reported `VestingEscrowFactory.recoverERC20` issue.

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

**File:** docs/DataStructures/Account.md (L151-165)
```markdown
```rust
pub enum DeterministicAccountStateInit {
    V1(DeterministicAccountStateInitV1),
}

pub struct DeterministicAccountStateInitV1 {
    pub code: GlobalContractIdentifier,
    pub data: BTreeMap<Vec<u8>, Vec<u8>>,
}

pub enum GlobalContractIdentifier {
    CodeHash(CryptoHash) = 0,
    AccountId(AccountId) = 1,
}
```
```
