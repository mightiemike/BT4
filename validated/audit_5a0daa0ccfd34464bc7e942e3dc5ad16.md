Found it. This is a directly analogous "wrong permission gate" bug: `check_actor_permissions` in `runtime/runtime/src/actions.rs` is the single authorization gate that decides which actions require `actor_id == account_id` (i.e., only the account owner, via a `FunctionCall`-triggered self-receipt or a signed tx, may perform the action). Just like the audit finding — where `transferTokens()` should require `onlyOperator` but was wired to the more permissive `onlyExtension` — nearcore's `check_actor_permissions` explicitly puts `Action::TransferToGasKey(_)` in the *no-check* arm alongside harmless actions like `Transfer`/`FunctionCall`, while its sibling `WithdrawFromGasKey` correctly requires `actor_id == account_id`: [1](#0-0) 

### Title
Missing actor-permission check on `TransferToGasKey` lets any predecessor fund a gas key it does not own - (File: runtime/runtime/src/actions.rs)

### Summary
`check_actor_permissions` gates administrative/self-only actions (`DeployContract`, `Stake`, `AddKey`, `DeleteKey`, `DeployGlobalContract`, `UseGlobalContract`, `WithdrawFromGasKey`) behind `actor_id == account_id`, but `Action::TransferToGasKey` is placed in the unrestricted arm together with `CreateAccount`/`FunctionCall`/`Transfer`, i.e. it has **no** actor check at all.

### Finding Description
`apply_action` in `runtime/runtime/src/lib.rs` calls `check_actor_permissions(action, account, actor_id, account_id)` before dispatching to the action handler for every action type, including `TransferToGasKey`/`WithdrawFromGasKey`: [2](#0-1) [3](#0-2) 

Inside `check_actor_permissions`, `WithdrawFromGasKey` is correctly included in the group requiring `actor_id == account_id` (else `ActionErrorKind::ActorNoPermission`), but `TransferToGasKey` is bucketed with `CreateAccount`, `FunctionCall`, `Transfer` — actions that are legitimately allowed to be executed by any predecessor against any receiver: [4](#0-3) 

The handler itself, `action_transfer_to_gas_key`, performs no additional caller check — it simply looks up the gas key by `(account_id, public_key)` and adds `action.deposit` to its balance: [5](#0-4) 

Because a receipt's `actor_id`/`account_id` model is what NEAR uses to gate "administrative" actions to the account owner (the same mechanism documented for `DeployContract`/`Stake`/`AddKey`/`DeleteKey` in the OpenAPI/OpenRPC spec: "can be proceed only if sender=receiver or the first TX action is a CreateAccount action"), the absence of this check on `TransferToGasKey` means the action executes for *any* predecessor sending a receipt to *any* receiver account, exactly analogous to the reported Solidity bug where a privileged recovery function was reachable via the wrong (over-permissive) access modifier.

### Impact Explanation
While crediting a gas key's `balance` is a "deposit" and the funds come from the caller's own attached deposit (so it is not itself a direct theft), the missing gate is a genuine authorization-model violation: `TransferToGasKey` is documented and designed as an account-owner-only administrative action (its balance backs gas payment and is even eligible to be burned on deletion up to `MAX_BALANCE_TO_BURN`), yet it can be triggered without the owner's `actor_id` ever matching `account_id`. This breaks the account-ownership invariant that all other gas-key/key-management actions enforce, and it is a state-transition divergence risk: any protocol logic, tooling, explorers, or future features that assume `TransferToGasKey` (like `WithdrawFromGasKey`, `AddKey`, `DeleteKey`) is owner-gated will be wrong, and it opens an avenue for third parties to grow another account's gas-key prepaid balance unpredictably (which is drawn down by `verify_and_charge_gas_key_tx_ephemeral` and whose leftover on deletion is *burned*, i.e. it affects `tokens_burnt`/supply accounting for an account the caller does not control) without the owner's consent. This is an invalid-state-transition / access-control class bug consistent with a Medium-severity finding.

### Likelihood Explanation
Trivially reachable: any unprivileged account can submit a `FunctionCall` action (or a plain `TransferToGasKey` action if permitted by their own access key) to send a receipt whose `receiver_id` is any other existing account with a gas key, attaching a `TransferToGasKey` action with an arbitrary deposit and a target `public_key` belonging to that account's gas key. No special privilege, validator role, or contract deployment is required — this is a single-transaction, RPC-submittable path.

### Recommendation
Add `Action::TransferToGasKey(_)` to the actor-restricted arm of `check_actor_permissions` (alongside `WithdrawFromGasKey`, `AddKey`, `DeleteKey`), requiring `actor_id == account_id`:

```rust
Action::DeployContract(_)
| Action::Stake(_)
| Action::AddKey(_)
| Action::DeleteKey(_)
| Action::DeployGlobalContract(_)
| Action::UseGlobalContract(_)
| Action::TransferToGasKey(_)
| Action::WithdrawFromGasKey(_) => {
    if actor_id != account_id {
        return Err(ActionErrorKind::ActorNoPermission { ... }.into());
    }
}
```

### Proof of Concept
1. Account `victim.near` has a gas key with `AccessKeyPermission::GasKeyFunctionCall`/`GasKeyFullAccess` (see `add_gas_key`, `runtime/runtime/src/access_keys.rs:194`).
2. Attacker account `attacker.near`, with no relationship to `victim.near`, submits a transaction (or a contract-triggered receipt) containing a single `Action::TransferToGasKey(TransferToGasKeyAction { public_key: victim_gas_key_pk, deposit })` with `receiver_id = victim.near`.
3. `apply_action` runs `check_actor_permissions` — since `TransferToGasKey` falls in the no-check arm, `actor_id` (`attacker.near`) is never compared to `account_id` (`victim.near`), and the check passes.
4. `action_transfer_to_gas_key` executes: `gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit)` — the victim's gas key balance changes without any authorization from `victim.near`, confirmed by the existing unit tests around `action_transfer_to_gas_key` in `runtime/runtime/src/access_keys.rs:1022-1068`, none of which assert an actor/predecessor check because none exists at that layer.

### Citations

**File:** runtime/runtime/src/actions.rs (L755-801)
```rust
pub(crate) fn check_actor_permissions(
    action: &Action,
    account: &Option<Account>,
    actor_id: &AccountId,
    account_id: &AccountId,
) -> Result<(), ActionError> {
    match action {
        Action::DeployContract(_)
        | Action::Stake(_)
        | Action::AddKey(_)
        | Action::DeleteKey(_)
        | Action::DeployGlobalContract(_)
        | Action::UseGlobalContract(_)
        | Action::WithdrawFromGasKey(_) => {
            if actor_id != account_id {
                return Err(ActionErrorKind::ActorNoPermission {
                    account_id: account_id.clone(),
                    actor_id: actor_id.clone(),
                }
                .into());
            }
        }
        Action::DeleteAccount(_) => {
            if actor_id != account_id {
                return Err(ActionErrorKind::ActorNoPermission {
                    account_id: account_id.clone(),
                    actor_id: actor_id.clone(),
                }
                .into());
            }
            let account = account.as_ref().unwrap();
            if !account.locked().is_zero() {
                return Err(ActionErrorKind::DeleteAccountStaking {
                    account_id: account_id.clone(),
                }
                .into());
            }
        }
        Action::CreateAccount(_)
        | Action::FunctionCall(_)
        | Action::Transfer(_)
        | Action::TransferToGasKey(_) => (),
        Action::Delegate(_) | Action::DelegateV2(_) => (),
        Action::DeterministicStateInit(_) | Action::UniversalStateInit(_) => (),
    };
    Ok(())
}
```

**File:** runtime/runtime/src/lib.rs (L599-611)
```rust
        // Account validation
        if let Err(e) =
            check_account_existence(action, account, account_id, &apply_state.config, receipt_shape)
        {
            result.result = Err(e);
            return Ok(result);
        }
        // Permission validation
        if let Err(e) = check_actor_permissions(action, account, actor_id, account_id) {
            result.result = Err(e);
            return Ok(result);
        }
        match action {
```

**File:** runtime/runtime/src/lib.rs (L803-821)
```rust
            Action::TransferToGasKey(transfer_to_gas_key) => {
                metrics::ACTION_CALLED_COUNT.transfer_to_gas_key.inc();
                action_transfer_to_gas_key(
                    state_update,
                    &mut result,
                    account_id,
                    transfer_to_gas_key,
                )?;
            }
            Action::WithdrawFromGasKey(withdraw_from_gas_key) => {
                metrics::ACTION_CALLED_COUNT.withdraw_from_gas_key.inc();
                action_withdraw_from_gas_key(
                    state_update,
                    account.as_mut().expect(EXPECT_ACCOUNT_EXISTS),
                    &mut result,
                    account_id,
                    withdraw_from_gas_key,
                )?;
            }
```

**File:** runtime/runtime/src/access_keys.rs (L257-288)
```rust
pub(crate) fn action_transfer_to_gas_key(
    state_update: &mut TrieUpdate,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &TransferToGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? else {
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        // Key exists but is not a gas key
        result.result = Err(ActionErrorKind::GasKeyDoesNotExist {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
        }
        .into());
        return Ok(());
    };

    gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "gas key balance integer overflow".to_string(),
        ))
    })?;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);
    Ok(())
}
```
