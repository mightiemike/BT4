### Title
Unpermissioned `TransferToGasKey` Lets Any Attacker Permanently Block Gas-Key and Account Deletion — (`runtime/runtime/src/actions.rs`, `runtime/runtime/src/access_keys.rs`)

---

### Summary

`check_actor_permissions` in `runtime/runtime/src/actions.rs` imposes no `actor_id == account_id` guard on `Action::TransferToGasKey`. Any unprivileged account can therefore fund any gas key on any other account. Because `delete_gas_key` in `runtime/runtime/src/access_keys.rs` hard-blocks deletion when `GasKeyInfo.balance > MAX_BALANCE_TO_BURN` (1 NEAR), and because `WithdrawFromGasKey` is explicitly unavailable as a promise host function (so it can only be issued from a transaction signed by a non-`GasKeyFunctionCall` key), an attacker can permanently prevent a victim whose only key is a `GasKeyFunctionCall` gas key from deleting that key or their account.

---

### Finding Description

**Root cause 1 — missing actor check on `TransferToGasKey`.**

`check_actor_permissions` in `runtime/runtime/src/actions.rs` explicitly exempts `TransferToGasKey` from the owner-only guard:

```rust
Action::CreateAccount(_)
| Action::FunctionCall(_)
| Action::Transfer(_)
| Action::TransferToGasKey(_) => (),   // ← no actor_id == account_id check
``` [1](#0-0) 

`action_transfer_to_gas_key` in `runtime/runtime/src/access_keys.rs` only verifies that the target gas key exists on the receiver account; it performs no caller-identity check:

```rust
pub(crate) fn action_transfer_to_gas_key(
    state_update: &mut TrieUpdate,
    result: &mut ActionResult,
    account_id: &AccountId,
    action: &TransferToGasKeyAction,
) -> Result<(), RuntimeError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, &action.public_key)? ...
    gas_key_info.balance = gas_key_info.balance.checked_add(action.deposit)...
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);
    Ok(())
}
``` [2](#0-1) 

**Root cause 2 — `delete_gas_key` hard-blocks deletion above 1 NEAR.**

```rust
if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { ... }.into());
    return Ok(());
}
``` [3](#0-2) 

`MAX_BALANCE_TO_BURN` is 1 NEAR: [4](#0-3) 

The same threshold blocks `action_delete_account`: [5](#0-4) 

**Root cause 3 — `WithdrawFromGasKey` is unavailable from contract execution.**

The `WithdrawFromGasKeyAction` struct carries an explicit design comment:

```rust
/// This action must only be available via transactions, not via contract execution
/// (there is no corresponding promise batch action host function).
``` [6](#0-5) 

`WithdrawFromGasKey` also requires `actor_id == account_id`: [7](#0-6) 

A `GasKeyFunctionCall` key is restricted to a single `FunctionCall` action at the verifier level; it cannot include a `WithdrawFromGasKey` action in the same transaction. Because there is no promise host function for `WithdrawFromGasKey`, a contract called via the gas key also cannot issue the withdrawal. The victim is therefore unable to drain the balance through any path reachable from a `GasKeyFunctionCall` key alone.

---

### Impact Explanation

An attacker sends a `TransferToGasKey` action targeting the victim's `GasKeyFunctionCall` gas key with a deposit that pushes `GasKeyInfo.balance` above 1 NEAR. After this:

- `action_delete_key` returns `GasKeyBalanceTooHigh` — the victim cannot delete the gas key.
- `action_delete_account` returns `GasKeyBalanceTooHigh` — the victim cannot delete their account.
- `WithdrawFromGasKey` is unreachable from a `GasKeyFunctionCall` key — the victim cannot drain the balance.

The victim's account balance is not directly stolen, but the victim permanently loses the ability to revoke the gas key or close the account. The attacker must keep topping up the balance (spending their own NEAR) to sustain the lock, but the cost is low relative to the damage. This is a **balance manipulation** (adversary writes to victim's `GasKeyInfo.balance` without consent) causing a **non-network-level DoS** on key and account lifecycle management, both of which are in scope.

---

### Likelihood Explanation

The scenario requires the victim to hold only a `GasKeyFunctionCall` gas key with no other access keys. This is a realistic configuration for relayer-funded accounts (NEP-366 meta-transaction users who have no NEAR of their own). The attacker needs only to know the victim's account ID and gas key public key (both publicly visible on-chain) and to spend slightly more than 1 NEAR. No privileged access, validator control, or key compromise is required.

---

### Recommendation

Add an `actor_id == account_id` guard to `TransferToGasKey` in `check_actor_permissions`, consistent with the guard already applied to `WithdrawFromGasKey`, `AddKey`, `DeleteKey`, and `DeployContract`. Alternatively, allow the account owner to delete a gas key regardless of balance (burning up to `MAX_BALANCE_TO_BURN` and refunding the remainder to the account), or provide a promise host function for `WithdrawFromGasKey` so that a `GasKeyFunctionCall` key can reach the withdrawal path via a self-call.

---

### Proof of Concept

1. **Setup**: Alice creates account `alice.near` with a single `GasKeyFunctionCall` gas key `K` (restricted to calling `ft_transfer` on `token.near`). Alice has no other access keys.

2. **Attack**: Bob submits a transaction:
   ```
   Transaction {
     signer_id: "bob.near",
     receiver_id: "alice.near",
     actions: [TransferToGasKey { public_key: K, deposit: 1_000_000_000_000_000_000_000_001 }]
   }
   ```
   This is accepted because `check_actor_permissions` imposes no restriction on `TransferToGasKey`. [1](#0-0) 

3. **State after attack**: `alice.near`'s gas key `K` now has `GasKeyInfo.balance = 1.000...001 NEAR > MAX_BALANCE_TO_BURN`.

4. **Alice tries to delete key `K`**:
   ```
   action_delete_key → delete_gas_key → GasKeyBalanceTooHigh { balance: 1.000...001 NEAR }
   ```
   Fails. [8](#0-7) 

5. **Alice tries to delete her account**:
   ```
   action_delete_account → compute_gas_key_balance_sum → GasKeyBalanceTooHigh
   ```
   Fails. [9](#0-8) 

6. **Alice tries to withdraw**: Alice cannot include `WithdrawFromGasKey` in a transaction signed by `K` (the key's `GasKeyFunctionCall` permission restricts it to a single `FunctionCall` action). No promise host function exists for `WithdrawFromGasKey`, so a contract call via `K` also cannot issue the withdrawal. [6](#0-5) 

7. **Bob sustains the lock**: Each time Alice's gas-key balance drops toward 1 NEAR through normal usage, Bob sends another small `TransferToGasKey` deposit to keep it above the threshold. Alice's gas key and account are permanently unmanageable.

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

**File:** runtime/runtime/src/actions.rs (L718-731)
```rust
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
```

**File:** runtime/runtime/src/actions.rs (L749-752)
```rust
        Action::CreateAccount(_)
        | Action::FunctionCall(_)
        | Action::Transfer(_)
        | Action::TransferToGasKey(_) => (),
```

**File:** runtime/runtime/src/access_keys.rs (L103-111)
```rust
    if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: Some(Box::new(public_key.clone())),
            balance: gas_key_info.balance,
        }
        .into());
        return Ok(());
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

**File:** core/primitives-core/src/account.rs (L551-555)
```rust
impl GasKeyInfo {
    /// Maximum gas key balance that can be burned during key or account deletion.
    /// Deletion fails if the (sum of) gas key balance(s) exceeds this threshold.
    pub const MAX_BALANCE_TO_BURN: Balance = Balance::from_near(1);

```

**File:** core/primitives/src/action/mod.rs (L311-314)
```rust
/// Withdraw NEAR from a gas key's balance to the account.
///
/// This action must only be available via transactions, not via contract execution
/// (there is no corresponding promise batch action host function).
```
