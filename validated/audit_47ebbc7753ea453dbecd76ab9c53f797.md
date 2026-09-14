### Title
Deposits sent to a deterministic account whose referenced global contract is never deployed become permanently frozen - ([File: runtime/runtime/src/deterministic_account_id.rs])

### Summary
A `Transfer` to a `0s…` deterministic account id (NEP-616) is allowed to create the account before its `DeterministicStateInitAction` ever runs, exactly like the reported pattern of a resource reaching a "concluded"/committed state before its required configuration step is guaranteed to happen. If the `GlobalContractIdentifier` embedded in the account's `DeterministicAccountStateInit` never resolves to a deployed global contract, the state-init action can never succeed, and the pre-funded account has no other path (no key, no actor authority) to ever move or reclaim its balance.

### Finding Description
A deterministic account (`0s…`) can be pre-funded with a bare `Transfer` before its one-time `DeterministicStateInitAction` executes. `check_account_existence` explicitly allows `Transfer` to create it as the sole action (`implicit_creation_allowed`), and creation goes through `action_implicit_account_creation_transfer` → `create_deterministic_account`, producing an `Account::Initialized` record with `AccountContract::None` and **no actor-id claim**: [1](#0-0) [2](#0-1) 

The comment in `create_deterministic_account` is explicit that actor_id purposefully stays the predecessor "to prevent hijacking the account… preventing `AddKey`, `DeployContract`, or any other actions that only the account owner is permitted to do." Unlike `CreateAccount`, nothing ever assigns `actor_id = account_id` for this account, and because it is an implicit account type, `CreateAccount` on it is rejected (`OnlyImplicitAccountCreationAllowed`): [3](#0-2) 

Consequently, the only actions `check_account_existence` allows on this account afterward are another `Transfer` (adds more funds) or `DeterministicStateInitAction` (the only actions exempted from the actor/initialization checks): [4](#0-3) 

The state-init action's actual promotion from "uninit" to "active" requires resolving the account's committed `GlobalContractIdentifier` via `use_global_contract`: [5](#0-4) [6](#0-5) 

If that referenced global contract (by `CodeHash` or `AccountId`) is never deployed — e.g. the id was mistyped, references an account that never calls `DeployGlobalContract`, or is a griefing address deliberately crafted with a `code` identifier the attacker controls and simply never deploys — every attempt at `DeterministicStateInitAction` fails execution with `GlobalContractDoesNotExist`, documented in `docs/RuntimeSpec/Actions.md:519-520`. Because the account can never acquire an access key or a matching `actor_id`, no `AddKey`, `DeployContract`, or `DeleteAccount` can ever be authorized against it (`check_actor_permissions` requires actor==account, which is unreachable), so any balance sent to it via `Transfer` (documented as an explicitly supported "pre-pay for storage" pattern, `docs/DataStructures/Account.md:185-186`, and exercised by `test_deterministic_state_init_prepay_for_storage`) is permanently locked with no possible recovery path. [7](#0-6) 

This mirrors the reported bug class exactly: the runtime lets a value-holding entity reach a state ("account exists and holds funds") without validating that its unlock precondition (a deployed global contract enabling later `AddKey`/`DeleteAccount`) will ever be satisfiable, and once that precondition is permanently unsatisfiable, the funds are stuck forever with no cancellation/refund mechanism.

### Impact Explanation
Any unprivileged transaction signer can send a plain `Transfer` to a `0s…` account id whose derivation commits to a `GlobalContractIdentifier` that is not deployed and never will be (this is trivial to construct: an attacker computes a `DeterministicAccountStateInit` referencing an account id they never intend to deploy a global contract from, publishes/derives the resulting `0s…` id, and any depositor's funds sent there — by mistake or by being lured — are irrecoverably frozen). This satisfies "permanently frozen funds," a concrete state-transition-acceptance failure with no way for a user or protocol-level path to reclaim the NEAR.

### Likelihood Explanation
Reachable purely via a standard, permissionless `Transfer` transaction — no validator, node, or protocol privilege is required. The precondition (the referenced global contract never being deployed) is entirely plausible: typos in the encoded `GlobalContractIdentifier::AccountId`, a global-contract deployer who changes plans, or deliberate griefing by publishing an account id/derived deterministic id that will never host the referenced contract.

### Recommendation
Do not allow `Transfer`-only creation of a deterministic (`0s…`) account to leave it in a state where it can never be unlocked. Options:
- Require `DeterministicStateInitAction` and `Transfer` to be resolved atomically (reject bare pre-funding transfers to `0s…` accounts, forcing the full path to include a valid, already-deployed `GlobalContractIdentifier`), or
- Add a way for a `0s…` account that has never completed state-init after some elapsed epochs to be swept/refunded to its original depositor(s), or
- Validate at the `DeterministicStateInitAction`/derivation stage that the referenced global contract already exists before allowing any funds to be attached to the derived id.

### Proof of Concept
1. Choose (or mistype) a `GlobalContractIdentifier::AccountId("victim-will-never-deploy.near")` that will never have a global contract deployed under it.
2. Build `DeterministicAccountStateInitV1 { code: that identifier, data: {} }`, derive the `0s…` account id (`derive_near_deterministic_account_id`).
3. Send a plain `Transfer` with a deposit to that `0s…` id — succeeds and creates the account via `action_implicit_account_creation_transfer` → `create_deterministic_account` (see `test_deterministic_state_init_prepay_for_storage`, `test-loop-tests/src/tests/deterministic_account_id.rs:462-504`, which demonstrates exactly this "pre-pay, account created but not usable" flow).
4. Attempt `DeterministicStateInitAction` against that account — it fails permanently with `GlobalContractDoesNotExist` since the referenced global contract was never deployed.
5. Attempt any other action (`AddKey`, `DeployContract`, `DeleteAccount`) on the account — all fail because `actor_id` can never be made to equal the account id (per the invariant documented in `deterministic_account_id.rs:117-133` and enforced by `check_account_existence`/`check_actor_permissions`).
6. The deposited balance remains on the account indefinitely with no transaction path to move or reclaim it.

### Citations

**File:** runtime/runtime/src/actions.rs (L270-275)
```rust
        AccountType::NearDeterministicAccount => {
            *account = Some(create_deterministic_account(
                deposit,
                &apply_state.config.fees.storage_usage_config,
            ));
        }
```

**File:** runtime/runtime/src/actions.rs (L821-841)
```rust
    match action {
        Action::CreateAccount(_) => {
            if account.is_some() {
                return Err(ActionErrorKind::AccountAlreadyExists {
                    account_id: account_id.clone(),
                }
                .into());
            }
            if get_account_type(account_id, config).is_implicit() {
                // Implicit accounts can only be created implicitly.
                // `CreateAccount` claims `actor_id` for the new account, which
                // would let the rest of the receipt add an access key to an id
                // whose private key the sender does not hold. Rejecting the action
                // is the simplest way to close that.
                // See https://github.com/nearprotocol/NEPs/pull/71
                return Err(ActionErrorKind::OnlyImplicitAccountCreationAllowed {
                    account_id: account_id.clone(),
                }
                .into());
            }
        }
```

**File:** runtime/runtime/src/actions.rs (L851-889)
```rust
        Action::DeterministicStateInit(_) => {
            // Both existing and non-existing is valid for DeterministicStateInit.
            // Does not exist => The account will be created by the action.
            // Does exist => Nothing happens but the receipt is not aborted to
            // allow optional init before other actions.
        }
        Action::UniversalStateInit(_) => {
            // A missing account is created by the action, an uninitialized one
            // (funded by an earlier transfer) gets its state installed, and an
            // initialized one is left untouched.
        }
        Action::DeployContract(_)
        | Action::FunctionCall(_)
        | Action::Stake(_)
        | Action::AddKey(_)
        | Action::DeleteKey(_)
        | Action::DeleteAccount(_)
        | Action::Delegate(_)
        | Action::DelegateV2(_)
        | Action::DeployGlobalContract(_)
        | Action::UseGlobalContract(_)
        | Action::TransferToGasKey(_)
        | Action::WithdrawFromGasKey(_) => {
            let Some(account) = account else {
                return Err(ActionErrorKind::AccountDoesNotExist {
                    account_id: account_id.clone(),
                }
                .into());
            };
            // An uninitialized `0u` account has no access keys, code or data, so
            // for everything but its own state init and a transfer it is as good
            // as absent.
            if !account.is_initialized() {
                return Err(ActionErrorKind::AccountNotInitialized {
                    account_id: account_id.clone(),
                }
                .into());
            }
        }
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L15-65)
```rust
pub(crate) fn action_deterministic_state_init(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    maybe_account: &mut Option<Account>,
    account_id: &AccountId,
    receipt: &Receipt,
    action: &DeterministicStateInitAction,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    // See https://github.com/near/NEPs/blob/master/neps/nep-0616.md#account-state
    // for the detailed description around deterministic account state.
    let storage_usage_config = &apply_state.config.fees.storage_usage_config;
    let account = match maybe_account {
        Some(account) => account,
        None => {
            // cspell:ignore nonexist
            // `nonexist` -> `uninit` account state transition
            // Create with zero balance now and check later how much of the
            // provided deposit is needed.
            let new_account = create_deterministic_account(Balance::ZERO, storage_usage_config);
            maybe_account.insert(new_account)
        }
    };
    if account.contract().is_none() {
        // `uninit` -> `active` account state transition. "uninit" here is the
        // NEP-616 sense, a deterministic account with no contract yet, not
        // `Account::Uninitialized`: a `0u` id can never reach this, because
        // `validate_deterministic_state_init` pins the receiver to the derived
        // `0s` id.
        deploy_deterministic_account(
            state_update,
            account,
            account_id,
            &action.state_init,
            result,
            storage_usage_config,
        )?;
    }
    if result.result.is_err() {
        return Ok(());
    }

    settle_state_init_deposit(
        account,
        action.deposit,
        account_id,
        receipt,
        &apply_state.config,
        result,
    )
}
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L117-133)
```rust
pub(crate) fn create_deterministic_account(
    initial_balance: Balance,
    storage_usage_config: &StorageUsageConfig,
) -> Account {
    // Unlike `CreateAccount`, this account creation does not change
    // actor_id. This is important to prevent hijacking the account.
    // Actor id remains the predecessor, so any actions following will
    // be checked against that for actor permissions, preventing
    // `AddKey`, `DeployContract`, or any other actions that only the
    // account owner is permitted to do.
    Account::new(
        initial_balance,
        Balance::ZERO,
        AccountContract::None,
        storage_usage_config.num_bytes_account,
    )
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

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L462-504)
```rust
/// Ensure we can pre-pay the balance for a deterministic account.
///
/// It is a required feature that one can send a Transfer to a non-existing
/// deterministic account first and later initialize it without adding balance,
/// even if more storage than the ZBA limit is used.
#[test]
fn test_deterministic_state_init_prepay_for_storage() {
    let mut env = TestEnv::setup(Balance::from_near(100));
    env.deploy_global_contract(GlobalContractDeployMode::AccountId);

    let data = BTreeMap::from_iter([(b"key".to_vec(), vec![0u8; 100_000])]);
    let (state_init, det_account) = env.new_deterministic_account_with_data(data.clone());

    // Try once without pre-paying, must fail.
    let outcome = env
        .try_deploy_deterministic_account_with_data(data.clone(), Balance::ZERO)
        .expect("should be able to send transaction");
    assert_matches!(
        outcome.status,
        FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
            kind: ActionErrorKind::LackBalanceForState { .. },
            index: _
        }))
    );

    // Prepay
    let required_for_storage = env.balance_for_storage(state_init);
    env.fund_with_near_balance(det_account.clone(), required_for_storage);
    assert_eq!(
        required_for_storage,
        env.get_account_state(det_account.clone()).amount,
        "account should have been created and funded now"
    );

    // Contract can't be called, yet.
    env.assert_test_contract_not_usable_on_account(det_account.clone());

    // Try creating again, with zero balance again. Must succeed this time.
    env.try_deploy_deterministic_account_with_data(data, Balance::ZERO)
        .expect("should be able to send transaction")
        .assert_success();
    env.assert_test_contract_usable_on_account(det_account);
}
```
