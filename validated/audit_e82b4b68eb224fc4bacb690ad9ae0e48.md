### Title
Gas key balance burn cap inconsistently enforced between `DeleteKey` (per-key) and `DeleteAccount` (aggregate) — (`runtime/runtime/src/access_keys.rs`)

---

### Summary

`GasKeyInfo::MAX_BALANCE_TO_BURN` (1 NEAR) is the declared upper bound on how much gas-key balance may be burned during a deletion. `action_delete_account` enforces this as an **aggregate** across all gas keys on the account, while `action_delete_key` / `delete_gas_key` enforces it only **per individual key**. An unprivileged account owner who holds N gas keys each funded just below 1 NEAR can delete them one-by-one, burning N × ~1 NEAR in total — far exceeding the cap that `DeleteAccount` would have blocked.

---

### Finding Description

**Cap declaration**

`GasKeyInfo::MAX_BALANCE_TO_BURN = Balance::from_near(1)` is documented as "Deletion fails if the (sum of) gas key balance(s) exceeds this threshold." [1](#0-0) 

**`DeleteKey` path — per-key check only**

`action_delete_key` dispatches to `delete_gas_key`, which checks only the single key's balance:

```rust
if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { ... });
    return Ok(());
}
result.tokens_burnt = result.tokens_burnt.checked_add(gas_key_info.balance)...;
``` [2](#0-1) 

**`DeleteAccount` path — aggregate check**

`action_delete_account` sums all gas-key balances and rejects if the total exceeds the cap:

```rust
let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { ... });
    return Ok(());
}
``` [3](#0-2) 

**The gap**

`DeleteKey` never reads the balances of the *other* gas keys on the account. Each individual deletion is evaluated in isolation. A user who funds three gas keys with 0.9 NEAR each can issue three `DeleteKey` transactions; each passes the per-key check (0.9 < 1 NEAR), burning 2.7 NEAR in total. A direct `DeleteAccount` on the same state would fail with `GasKeyBalanceTooHigh` (2.7 > 1 NEAR).

The `WithdrawFromGasKey` action correctly reduces a key's balance before deletion, but it does not help here because the attacker is *not* withdrawing — they are burning. [4](#0-3) 

---

### Impact Explanation

Gas-key balance is NEAR deposited by the account owner via `TransferToGasKey`. On `DeleteKey` it is added to `tokens_burnt` and permanently destroyed — it is **not** refunded to the account. [5](#0-4) 

By bypassing the aggregate cap, an account owner can irreversibly burn an arbitrarily large amount of their own NEAR (N × up to 1 NEAR per key). This is a **loss of funds** reachable from ordinary, unprivileged user actions (standard `DeleteKey` transactions). The cap's stated purpose — preventing large accidental burns — is defeated.

---

### Likelihood Explanation

The precondition is straightforward: create multiple gas keys and fund each with just under 1 NEAR via `TransferToGasKey`. No privileged role, no contract deployment, and no validator cooperation is required. The only constraint is that the account must hold enough NEAR to fund the keys in the first place.

---

### Recommendation

Enforce the aggregate cap inside `delete_gas_key` (or its caller `action_delete_key`) by summing the balances of all remaining gas keys on the account before proceeding:

```rust
// Proposed additional check in delete_gas_key:
let total_gas_key_balance = compute_gas_key_balance_sum(state_update, account_id)?;
if total_gas_key_balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { ... });
    return Ok(());
}
```

This mirrors the check already present in `action_delete_account` and closes the per-key bypass. [3](#0-2) 

---

### Proof of Concept

```
// Setup (access-key transactions, no special privileges)
1. AddKey  → gas_key_A  (GasKeyFullAccess, num_nonces=1)
2. AddKey  → gas_key_B  (GasKeyFullAccess, num_nonces=1)
3. AddKey  → gas_key_C  (GasKeyFullAccess, num_nonces=1)
4. TransferToGasKey { public_key: gas_key_A, deposit: 0.9 NEAR }
5. TransferToGasKey { public_key: gas_key_B, deposit: 0.9 NEAR }
6. TransferToGasKey { public_key: gas_key_C, deposit: 0.9 NEAR }

// Bypass (three DeleteKey transactions)
7. DeleteKey { public_key: gas_key_A }
   → delete_gas_key: 0.9 NEAR < 1 NEAR → OK, burns 0.9 NEAR
8. DeleteKey { public_key: gas_key_B }
   → delete_gas_key: 0.9 NEAR < 1 NEAR → OK, burns 0.9 NEAR
9. DeleteKey { public_key: gas_key_C }
   → delete_gas_key: 0.9 NEAR < 1 NEAR → OK, burns 0.9 NEAR

// Total burned: 2.7 NEAR — exceeds MAX_BALANCE_TO_BURN (1 NEAR)

// Counterfactual: DeleteAccount at step 7 would have failed:
//   compute_gas_key_balance_sum = 2.7 NEAR > 1 NEAR → GasKeyBalanceTooHigh
```

The three `DeleteKey` calls each pass the per-key guard in `delete_gas_key` at line 103 of `runtime/runtime/src/access_keys.rs`, while the equivalent `DeleteAccount` would have been blocked by the aggregate guard in `action_delete_account` at line 340 of `runtime/runtime/src/actions.rs`. [6](#0-5) [7](#0-6)

### Citations

**File:** core/primitives-core/src/account.rs (L551-554)
```rust
impl GasKeyInfo {
    /// Maximum gas key balance that can be burned during key or account deletion.
    /// Deletion fails if the (sum of) gas key balance(s) exceeds this threshold.
    pub const MAX_BALANCE_TO_BURN: Balance = Balance::from_near(1);
```

**File:** runtime/runtime/src/access_keys.rs (L52-91)
```rust
pub(crate) fn action_delete_key(
    config: &RuntimeConfig,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    delete_key: &DeleteKeyAction,
) -> Result<(), RuntimeError> {
    let access_key = get_access_key(state_update, account_id, &delete_key.public_key)?;
    if let Some(access_key) = access_key {
        if let Some(gas_key_info) = access_key.gas_key_info() {
            delete_gas_key(
                config,
                state_update,
                account,
                result,
                account_id,
                &delete_key.public_key,
                &access_key,
                gas_key_info,
            )?;
        } else {
            delete_regular_key(
                &config.fees,
                state_update,
                account,
                account_id,
                &delete_key.public_key,
                &access_key,
            );
        }
    } else {
        result.result = Err(ActionErrorKind::DeleteKeyDoesNotExist {
            public_key: delete_key.public_key.clone().into(),
            account_id: account_id.clone(),
        }
        .into());
    }
    Ok(())
}
```

**File:** runtime/runtime/src/access_keys.rs (L103-113)
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
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_info.balance).ok_or(IntegerOverflowError)?;
```

**File:** runtime/runtime/src/access_keys.rs (L315-334)
```rust
    let Some(updated_balance) = gas_key_info.balance.checked_sub(action.amount) else {
        result.result = Err(ActionErrorKind::InsufficientGasKeyBalance {
            account_id: account_id.clone(),
            public_key: Box::new(action.public_key.clone()),
            balance: gas_key_info.balance,
            required: action.amount,
        }
        .into());
        return Ok(());
    };
    gas_key_info.balance = updated_balance;
    set_access_key(state_update, account_id.clone(), action.public_key.clone(), &access_key);

    let new_account_balance = account.amount().checked_add(action.amount).ok_or_else(|| {
        RuntimeError::StorageError(StorageError::StorageInconsistentState(
            "Account balance integer overflow".to_string(),
        ))
    })?;
    account.set_amount(new_account_balance);
    Ok(())
```

**File:** runtime/runtime/src/actions.rs (L299-375)
```rust
pub(crate) fn action_delete_account(
    state_update: &mut TrieUpdate,
    account: &mut Option<Account>,
    actor_id: &mut AccountId,
    receipt: &Receipt,
    result: &mut ActionResult,
    account_id: &AccountId,
    delete_account: &DeleteAccountAction,
    config: &RuntimeConfig,
    current_protocol_version: ProtocolVersion,
) -> Result<(), StorageError> {
    let account_ref = account.as_ref().unwrap();
    let account_storage_usage = if ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
        .enabled(current_protocol_version)
    {
        let contract_storage = get_contract_storage_usage(state_update, account_id, account_ref)?;
        account_ref.storage_usage().saturating_sub(contract_storage)
    } else {
        // Legacy behavior: only subtracts local contract code, misses the
        // global contract identifier overhead.
        let account_storage_usage = account_ref.storage_usage();
        let code_len = get_code_len_or_default(
            state_update,
            account_id.clone(),
            account_ref.local_contract_hash().unwrap_or_default(),
        )?;
        debug_assert!(
            code_len == 0 || account_storage_usage > code_len,
            "account storage usage should be larger than code size. storage usage: {}, code size: {}",
            account_storage_usage,
            code_len
        );
        account_storage_usage.saturating_sub(code_len)
    };
    if account_storage_usage > Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE {
        result.result =
            Err(ActionErrorKind::DeleteAccountWithLargeState { account_id: account_id.clone() }
                .into());
        return Ok(());
    }
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
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_balance_to_burn).ok_or_else(|| {
            StorageError::StorageInconsistentState("tokens_burnt overflow".to_string())
        })?;
    if remove_result.gas_key_nonce_count > 0 {
        let compute = storage_removes_compute(
            &config.wasm_config.ext_costs,
            remove_result.gas_key_nonce_count,
            remove_result.gas_key_nonce_total_key_bytes,
            AccessKey::NONCE_VALUE_LEN * remove_result.gas_key_nonce_count,
        );
        result.compute_usage = safe_add_compute(result.compute_usage, compute).map_err(|_| {
            StorageError::StorageInconsistentState("compute_usage overflow".to_string())
        })?;
    }
    *actor_id = receipt.predecessor_id().clone();
    *account = None;
    Ok(())
}
```
