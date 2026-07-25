### Title
Unauthorized `TransferToGasKey` via Cross-Contract Call Enables Griefing That Permanently Locks Gas Key Deletion — (`File: runtime/runtime/src/actions.rs`)

### Summary

`check_actor_permissions` in `runtime/runtime/src/actions.rs` places `Action::TransferToGasKey` in the no-check arm alongside `Transfer` and `CreateAccount`. This means any cross-contract call (where `actor_id != account_id`) can execute a `TransferToGasKey` action on a victim's account. Because `delete_gas_key` and `action_delete_account` both abort with `GasKeyBalanceTooHigh` when a gas key's balance exceeds `MAX_BALANCE_TO_BURN = 1 NEAR`, an attacker can permanently prevent a victim from deleting their gas key or their account by continuously funding the key above that threshold.

### Finding Description

`check_actor_permissions` is the runtime's authorization gate for actions that mutate account state. Sensitive actions — `DeployContract`, `Stake`, `AddKey`, `DeleteKey`, `DeployGlobalContract`, `UseGlobalContract`, and `WithdrawFromGasKey` — all require `actor_id == account_id`. `TransferToGasKey` is explicitly excluded from this check:

```rust
// runtime/runtime/src/actions.rs lines 749–752
Action::CreateAccount(_)
| Action::FunctionCall(_)
| Action::Transfer(_)
| Action::TransferToGasKey(_) => (),   // ← no actor check
``` [1](#0-0) 

The `action_transfer_to_gas_key` handler unconditionally adds `action.deposit` to the gas key's `GasKeyInfo.balance` field on the target account:

```rust
// runtime/runtime/src/access_keys.rs lines 281–286
gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit)...?;
set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);
``` [2](#0-1) 

The deletion path for gas keys enforces a hard cap:

```rust
// runtime/runtime/src/access_keys.rs lines 103–112 (delete_gas_key)
if balance > MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { ... }.into());
    return Ok(());
}
``` [3](#0-2) 

The same check applies to account deletion:

```rust
// runtime/runtime/src/actions.rs lines 339–348
let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { ... }.into());
    return Ok(());
}
``` [4](#0-3) 

`WithdrawFromGasKey` is correctly protected (requires `actor_id == account_id`), so only the victim can withdraw. However, the attacker can re-fund the key immediately after each withdrawal, creating a sustained race condition. [5](#0-4) 

### Impact Explanation

An unprivileged attacker who knows a victim's gas key public key (observable on-chain) can:

1. Deploy a contract that calls `promise_batch_action_transfer_to_gas_key` targeting the victim's gas key with a deposit > 1 NEAR.
2. The receipt executes on the victim's shard; `action_transfer_to_gas_key` adds the deposit to the gas key's `GasKeyInfo.balance` without any authorization check.
3. The victim's gas key balance now exceeds `MAX_BALANCE_TO_BURN = 1 NEAR`.
4. Every subsequent `DeleteKey` or `DeleteAccount` action by the victim fails with `GasKeyBalanceTooHigh`.
5. The victim can call `WithdrawFromGasKey` to drain the balance below 1 NEAR, but the attacker can immediately re-fund it.

The broken invariant is: **an account owner must always be able to delete their own access keys and account**. The attacker corrupts the `GasKeyInfo.balance` field on a key they do not own, causing the runtime's own deletion guard to block the legitimate owner.

The `TransferToGasKey` host function (`promise_batch_action_transfer_to_gas_key`) is available to any deployed contract, making this reachable from ordinary user-submitted transactions. [6](#0-5) 

### Likelihood Explanation

- Gas keys are a new feature (protocol version 85, `GasKeys` feature flag). Any account that has added a gas key is a potential victim.
- The attacker's cost is > 1 NEAR per funding round, which is non-trivial but affordable.
- The attack is fully permissionless: no privileged role, no key compromise, no social engineering. Any deployed contract can issue the cross-contract call.
- The victim can attempt to withdraw and delete in the same block, but the attacker can front-run with a re-fund in the next block.

### Recommendation

Add `Action::TransferToGasKey` to the actor-permission check arm in `check_actor_permissions`, requiring `actor_id == account_id`:

```rust
Action::DeployContract(_)
| Action::Stake(_)
| Action::AddKey(_)
| Action::DeleteKey(_)
| Action::DeployGlobalContract(_)
| Action::UseGlobalContract(_)
| Action::WithdrawFromGasKey(_)
| Action::TransferToGasKey(_) => {   // ← add here
    if actor_id != account_id {
        return Err(ActionErrorKind::ActorNoPermission { ... }.into());
    }
}
```

This preserves the ability for the account owner to fund their own gas keys (including via self-calls from contracts they control) while preventing third-party contracts from modifying gas key balances on accounts they do not own.

Alternatively, if permissionless funding is a desired protocol property, the `GasKeyBalanceTooHigh` guard on deletion should be replaced with a forced-burn path that allows the account owner to delete a gas key regardless of its balance, burning the full balance unconditionally.

### Proof of Concept

1. Victim `alice.near` adds a gas key with public key `K` via `AddKey`.
2. Attacker deploys `evil.near` with the following logic in its `attack` method:
   ```
   promise = promise_batch_create("alice.near")
   promise_batch_action_transfer_to_gas_key(promise, K, 2_000_000_000_000_000_000_000_000)  // 2 NEAR
   ```
3. Attacker calls `evil.near::attack` with 2 NEAR attached.
4. The receipt executes on Alice's shard; `action_transfer_to_gas_key` adds 2 NEAR to key `K`'s `GasKeyInfo.balance`. No actor permission check fires.
5. Alice submits `DeleteKey { public_key: K }`. The runtime calls `delete_gas_key`, which checks `balance (2 NEAR) > MAX_BALANCE_TO_BURN (1 NEAR)` and returns `GasKeyBalanceTooHigh`. The key is not deleted.
6. Alice submits `WithdrawFromGasKey { public_key: K, amount: 1.5 NEAR }`. Balance drops to 0.5 NEAR.
7. Attacker immediately re-funds with another 1.6 NEAR. Balance is now 2.1 NEAR.
8. Alice's `DeleteKey` fails again. The cycle repeats indefinitely at the attacker's discretion. [7](#0-6) [8](#0-7)

### Citations

**File:** runtime/runtime/src/actions.rs (L339-348)
```rust
    let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
    if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: None,
            balance: gas_key_balance_to_burn,
        }
        .into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L711-757)
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
        Action::DeterministicStateInit(_) => (),
    };
    Ok(())
}
```

**File:** runtime/runtime/src/access_keys.rs (L93-112)
```rust
fn delete_gas_key(
    config: &RuntimeConfig,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    public_key: &PublicKey,
    access_key: &AccessKey,
    gas_key_info: &GasKeyInfo,
) -> Result<(), RuntimeError> {
    if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: Some(Box::new(public_key.clone())),
            balance: gas_key_info.balance,
        }
        .into());
        return Ok(());
    }
    result.tokens_burnt =
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

**File:** runtime/runtime/src/receipt_manager.rs (L424-434)
```rust
    pub(super) fn append_action_transfer_to_gas_key(
        &mut self,
        receipt_index: ReceiptIndex,
        public_key: PublicKey,
        deposit: Balance,
    ) {
        self.append_action(
            receipt_index,
            Action::TransferToGasKey(Box::new(TransferToGasKeyAction { public_key, deposit })),
        );
    }
```
