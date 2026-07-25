### Title
Gas Key Balance Permanently Locked When Account Loses All Full-Access Keys With `GasKeyFunctionCall` Key Balance Exceeding `MAX_BALANCE_TO_BURN` — (`runtime/runtime/src/access_keys.rs`)

---

### Summary

The nearcore runtime introduces a gas key deletion guard (`GasKeyBalanceTooHigh`) that blocks `DeleteKey` and `DeleteAccount` when a gas key's balance exceeds `GasKeyInfo::MAX_BALANCE_TO_BURN` (1 NEAR). The intended recovery path is `WithdrawFromGasKey`, which moves balance from the gas key back to the account. However, `WithdrawFromGasKey` is a non-`FunctionCall` action and is therefore rejected by `verify_function_call_permission` when the signing key has `GasKeyFunctionCall` (or `FunctionCall`) permission. If an account's only remaining keys are `GasKeyFunctionCall` gas keys whose balance exceeds 1 NEAR, the balance is permanently unrecoverable: deletion is blocked, withdrawal is blocked, and account deletion is blocked. This is the nearcore analog of M-30: a state transition (key/account deletion) is blocked without a guaranteed recovery path for the underlying funds.

---

### Finding Description

**Invariant broken**: Every NEAR token deposited into a gas key via `TransferToGasKey` must be either spendable as gas, withdrawable via `WithdrawFromGasKey`, or burnable on key deletion. When the balance exceeds `MAX_BALANCE_TO_BURN` and the account has no key capable of submitting `WithdrawFromGasKey`, the balance is permanently frozen in the trie.

**Root cause — `delete_gas_key` blocks without a recovery path:** [1](#0-0) 

The guard correctly prevents burning > 1 NEAR, but returns an error and leaves the key intact. There is no automatic partial-withdrawal or forced-refund step.

**Root cause — `WithdrawFromGasKey` is blocked for `GasKeyFunctionCall` keys:**

The gas-key verifier calls `verify_function_call_permission` for any key whose `permission.function_call_permission()` returns `Some`, which includes `GasKeyFunctionCall`: [2](#0-1) 

`verify_function_call_permission` requires the transaction to contain exactly one `FunctionCall` action. A `WithdrawFromGasKey` action is not a `FunctionCall`, so it is rejected with `RequiresFullAccess`. The same restriction applies to regular `FunctionCall` keys on the regular-tx path.

**Root cause — `action_delete_account` also blocked:** [3](#0-2) 

Account deletion is blocked when the aggregate gas key balance exceeds 1 NEAR, with no automatic withdrawal step.

**`GasKeyInfo.MAX_BALANCE_TO_BURN` constant:** [4](#0-3) 

**`GasKeyFunctionCall` permission variant:** [5](#0-4) 

**Intentional absence of `WithdrawFromGasKey` from contract host functions** (confirming it must come from a transaction, not a contract): [6](#0-5) 

**Reachable state sequence (unprivileged user):**

1. Account `alice.near` holds regular `FullAccess` key K1 and gas key GK1 with `GasKeyFunctionCall` permission, funded with 5 NEAR via `TransferToGasKey`.
2. Alice submits a transaction signed by K1 containing `DeleteKeyAction { public_key: K1 }`.
3. K1 is deleted. The account now has only GK1.
4. Alice attempts `DeleteKeyAction { public_key: GK1 }` → `GasKeyBalanceTooHigh { balance: 5 NEAR }`.
5. Alice attempts `WithdrawFromGasKey { public_key: GK1, amount: 4 NEAR }` signed by GK1 → `RequiresFullAccess` (GK1 has `GasKeyFunctionCall` permission, cannot submit non-`FunctionCall` actions).
6. Alice attempts `DeleteAccountAction` → `GasKeyBalanceTooHigh`.
7. 5 NEAR is permanently frozen in GK1's trie entry.

---

### Impact Explanation

**Loss of funds**: The gas key balance (up to arbitrarily large amounts, since `TransferToGasKey` has no upper bound) is permanently unrecoverable. The tokens remain in the trie but cannot be spent, withdrawn, or burned. This is a direct loss of NEAR tokens for the account owner.

**Contract execution flow breakage**: Any contract account that ends up in this state (e.g., a contract that uses gas keys for relaying and whose deployer deleted the regular full-access key) cannot be cleaned up or have its funds recovered.

---

### Likelihood Explanation

The trigger requires two deliberate steps: (1) funding a `GasKeyFunctionCall` gas key with > 1 NEAR, and (2) deleting all regular full-access and `GasKeyFullAccess` keys. A user who does not understand the interaction between `MAX_BALANCE_TO_BURN` and the `FunctionCall` permission restriction can reach this state. The protocol provides no warning or guard at the `DeleteKey` step to prevent the user from locking themselves out of their gas key balance. The `GasKeys` feature is new (protocol version 85), so users and tooling authors may not yet be aware of this edge case.

---

### Recommendation

1. **Guard at `action_delete_key` / `action_delete_account`**: Before deleting a regular full-access key or `GasKeyFullAccess` key, check whether the deletion would leave the account with no key capable of submitting `WithdrawFromGasKey`. If so, and if any remaining gas key has balance > `MAX_BALANCE_TO_BURN`, reject the deletion with a descriptive error.

2. **Allow `WithdrawFromGasKey` via `GasKeyFunctionCall` keys**: Relax the `verify_function_call_permission` check to permit `WithdrawFromGasKey` actions even on function-call-restricted gas keys, since this action only moves balance back to the account owner and cannot be used to harm third parties.

3. **Auto-withdraw on deletion**: In `delete_gas_key`, instead of blocking when `balance > MAX_BALANCE_TO_BURN`, automatically move the balance back to `account.amount` (as `action_withdraw_from_gas_key` does) before removing the key, eliminating the burn-vs-stuck dilemma entirely.

---

### Proof of Concept

```
// Step 1: Fund gas key beyond MAX_BALANCE_TO_BURN
Transaction signed by K1 (FullAccess):
  receiver: alice.near
  actions:
    - AddKey { public_key: GK1, access_key: GasKeyFunctionCall(GasKeyInfo { balance: 0, num_nonces: 1 }, ...) }
    - TransferToGasKey { public_key: GK1, deposit: 2_000_000_000_000_000_000_000_000 }  // 2 NEAR

// Step 2: Delete the only full-access key
Transaction signed by K1 (FullAccess):
  receiver: alice.near
  actions:
    - DeleteKey { public_key: K1 }

// Step 3: Attempt to withdraw — FAILS with RequiresFullAccess
Transaction signed by GK1 (GasKeyFunctionCall):
  receiver: alice.near
  actions:
    - WithdrawFromGasKey { public_key: GK1, amount: 1_500_000_000_000_000_000_000_000 }
// Error: RequiresFullAccess (GasKeyFunctionCall key cannot submit non-FunctionCall actions)

// Step 4: Attempt to delete gas key — FAILS with GasKeyBalanceTooHigh
Transaction signed by GK1 (GasKeyFunctionCall):
  receiver: alice.near
  actions:
    - FunctionCall { ... }  // only valid action for GK1
// DeleteKey { public_key: GK1 } → GasKeyBalanceTooHigh { balance: ~2 NEAR }

// Result: 2 NEAR permanently frozen in GK1's trie entry.
// alice.near cannot delete GK1, cannot withdraw from GK1, cannot delete the account.
```

The exact error path for the withdrawal rejection is `verify_function_call_permission` at `runtime/runtime/src/verifier.rs:461–465`, which calls into the check at `verifier.rs:167–176` requiring a single `FunctionCall` action. The exact error path for the deletion block is `delete_gas_key` at `runtime/runtime/src/access_keys.rs:103–110`.

### Citations

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

**File:** runtime/runtime/src/verifier.rs (L461-465)
```rust
    if let Some(function_call_permission) = access_key.permission.function_call_permission()
        && let Err(e) = verify_function_call_permission(function_call_permission, tx)
    {
        return TxVerdict::Failed(e);
    }
```

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

**File:** core/primitives-core/src/account.rs (L551-555)
```rust
impl GasKeyInfo {
    /// Maximum gas key balance that can be burned during key or account deletion.
    /// Deletion fails if the (sum of) gas key balance(s) exceeds this threshold.
    pub const MAX_BALANCE_TO_BURN: Balance = Balance::from_near(1);

```

**File:** core/primitives-core/src/account.rs (L580-586)
```rust
    /// Gas key with limited permission to make transactions with FunctionCallActions
    /// Gas keys are a kind of access keys with a prepaid balance to pay for gas.
    GasKeyFunctionCall(GasKeyInfo, FunctionCallPermission),
    /// Gas key with full access to the account.
    /// Gas keys are a kind of access keys with a prepaid balance to pay for gas.
    GasKeyFullAccess(GasKeyInfo),
}
```

**File:** runtime/near-vm-runner/src/imports.rs (L291-295)
```rust
    // NOTE: There are intentionally no promise batch actions for
    // WithdrawFromGasKey. Actions that reduce gas key balance must only be
    // initiated via transactions, not by contracts. Otherwise, they will not be
    // visible to the pending transaction queue. Do not add host functions for
    // them. See NEP-611 for details.
```
