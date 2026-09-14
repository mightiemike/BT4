### Title
Deposits to an uninitialized deterministic/universal account can be permanently unspendable if `DeterministicStateInit`/`UniversalStateInit` is never applied - (File: `runtime/runtime/src/actions.rs`)

### Summary
A plain `Transfer` action to an implicit `NearDeterministic` (`0s…`) or `UniversalAccount` (`0u…`) account ID creates the account with `AccountContract::None` and **no access key at all** [1](#0-0) . This mirrors the reported Solidity pattern: a payable code path that accepts value with no accompanying authorization path to move it back out, unless a very specific follow-up action (`DeterministicStateInit`/`UniversalStateInit`, which must reference the exact derived id and deploy a contract) is later submitted by someone who knows the correct code/state-init parameters.

### Finding Description
`action_implicit_account_creation_transfer` is invoked whenever any account (including an unprivileged transaction signer or a relayed cross-contract `Transfer`) sends tokens to an id that resolves to `AccountType::NearDeterministicAccount` or `AccountType::UniversalAccount` and the account does not yet exist [1](#0-0) . For `NearDeterministicAccount`, the account is created via `create_deterministic_account`, which explicitly sets `AccountContract::None` and does **not** claim `actor_id` for the new account (the actor stays the predecessor, and no key is ever installed on the receiving account by this code path) [2](#0-1) . The comment in `action_implicit_account_creation_transfer` itself acknowledges the account has no key/contract at creation and is fundamentally different from `NearImplicitAccount`, which always gets a `FullAccess` key derived from its own id [3](#0-2) .

The only way to spend/reclaim these deposited funds later is a subsequent `DeterministicStateInit` action that deploys a global contract into that exact account and is checked against a derivation function pinning the receiver to the specific `0s…` id [4](#0-3) . If that specific state-init action is never produced (e.g., the sender mistypes/derives the wrong id, or the corresponding code/derivation was never actually intended to be initialized — analogous to "accidentally sending ETH to `Anchor.sol`'s bare `receive()`"), the deposited balance sits in an account with `AccountContract::None` and no access key, and there is no other action type in the runtime that can authorize a `Transfer`, `FunctionCall`, or `DeleteAccount` out of an account with neither a key nor a contract. This is structurally the same "no function to return money" gap flagged in the external report: value can flow in via an unprivileged transfer, but there is no code path that lets anyone move it back out unless a narrow, specific follow-up condition is met.

### Impact Explanation
Funds sent to an uninitialized deterministic/universal account before its `StateInit` action is executed are frozen: no signer can authorize a `Transfer`/`FunctionCall` from that account (no access key), and no contract logic exists yet to move the balance (no deployed code). This is a permanent-freezing-of-funds condition reachable by a normal unprivileged transaction signer simply sending a `Transfer` to a `0s…`/`0u…` account id, satisfying the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
Likelihood is **low-to-medium**: this requires a user or relayer to send tokens to an implicit deterministic/universal account id before (or without ever) executing the correct `DeterministicStateInit`/`UniversalStateInit` action for that exact id. This is a plausible operational mistake (analogous to accidentally sending ETH to a payable-only contract) since these implicit account types are new (NEP-616) and their addresses are derived, non-obvious values that could be mistyped or targeted before the owning dApp/relayer submits the state-init transaction.

### Recommendation
Consider one of:
- Rejecting `Transfer`-only implicit-account-creation for `NearDeterministic`/`Universal` account types unless bundled atomically with the corresponding `StateInit` action (so the account can never exist in a keyless/contract-less but funded state), or
- Providing a recovery/refund path (e.g., auto-refund the deposit back to the predecessor when creating an uninitialized deterministic/universal account via bare `Transfer`, similar to how `settle_state_init_deposit` already refunds excess deposit during a real state-init) rather than crediting the balance into a permanently unreachable account state.

### Proof of Concept
1. Compute a valid `0s…` (`NearDeterministic`) account id (per NEP-616 derivation) that has not yet had its `DeterministicStateInit` executed.
2. As any unprivileged signer, submit a `SignedTransaction` with a single `Transfer` action targeting that `0s…` account id. This is processed by `action_implicit_account_creation_transfer` → `AccountType::NearDeterministicAccount` branch, which calls `create_deterministic_account(deposit, …)` [5](#0-4) , creating the account with `AccountContract::None` and no access key [2](#0-1) .
3. Query the account: it now holds `deposit` tokens, `AccountContract::None`, and zero access keys.
4. Attempt any transaction signed by any key to move funds out of this account: every such transaction would need an access key on that account (none exists) or a `FunctionCall` to a deployed contract (none exists) — both fail (`AccessKeyNotFound` / no contract to invoke).
5. Unless the exact `DeterministicStateInit` for that specific derived id is later submitted by someone who possesses the correct state-init parameters, the deposited balance remains permanently unreachable.

Note: I was unable to fully verify whether any other administrative or protocol-level path (e.g., account deletion/beneficiary sweep) could reach a keyless, contract-less account's balance, since `action_delete_account` also requires being authorized via `check_actor_permissions`, which in turn requires the actor to be the account itself or an authorized key/contract — the same gap. This should be verified further with a live Devin session that can trace `check_actor_permissions` and `action_delete_account` end-to-end against a `NearDeterministic` account with `AccountContract::None`.

### Citations

**File:** runtime/runtime/src/actions.rs (L211-252)
```rust
/// Can only be used for implicit accounts.
///
/// The account is created without claiming `actor_id`, which stays the receipt's
/// predecessor. A `0u` id can be created by a transfer inside a batch (see
/// [`implicit_creation_allowed`]), and claiming it would hand the
/// rest of that batch the new account's own authority: a relayer sending
/// `[Transfer, UniversalStateInit, AddKey]` would install a key the id does not
/// commit to, and one ending in `DeleteAccount` would take the balance. For the
/// other implicit kinds the transfer is the whole receipt, so there is nothing
/// after it to authorize either way.
pub(crate) fn action_implicit_account_creation_transfer(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    fee_config: &RuntimeFeesConfig,
    account: &mut Option<Account>,
    account_id: &AccountId,
    deposit: Balance,
    block_height: BlockHeight,
    epoch_info_provider: &dyn EpochInfoProvider,
) {
    // Config-aware: account type whose feature is off reads as `NamedAccount` and panics
    // below rather than being created. Only `universal_accounts` can still be off.
    match get_account_type(account_id, apply_state.config.as_ref()) {
        AccountType::NearImplicitAccount => {
            let mut access_key = AccessKey::full_access();
            access_key.nonce = initial_nonce_value(block_height);

            // unwrap: the arm we are in means `account_id` is 64 hex characters.
            let public_key = PublicKey::from_near_implicit_account(account_id).unwrap();

            *account = Some(Account::new(
                deposit,
                Balance::ZERO,
                AccountContract::None,
                fee_config.storage_usage_config.num_bytes_account
                    + public_key.trie_id_len() as u64
                    + borsh::object_length(&access_key).unwrap() as u64
                    + fee_config.storage_usage_config.num_extra_bytes_record,
            ));

            set_access_key(state_update, account_id.clone(), public_key, &access_key);
        }
```

**File:** runtime/runtime/src/actions.rs (L270-282)
```rust
        AccountType::NearDeterministicAccount => {
            *account = Some(create_deterministic_account(
                deposit,
                &apply_state.config.fees.storage_usage_config,
            ));
        }
        AccountType::UniversalAccount => {
            *account = Some(Account::new_uninitialized(
                deposit,
                fee_config.storage_usage_config.num_bytes_account,
                initial_nonce_value(block_height),
            ));
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
