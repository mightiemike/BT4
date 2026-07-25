### Title
Missing Actor Permission Check on `TransferToGasKey` Allows Any Contract to Lock Victim's Gas Key and Block Account Deletion — (`runtime/runtime/src/actions.rs`)

### Summary

`check_actor_permissions` explicitly exempts `Action::TransferToGasKey` from the `actor_id != account_id` guard that protects all other balance-mutating key actions. Any deployed contract can call the `promise_batch_action_transfer_to_gas_key` host function to credit an arbitrary amount of NEAR into a victim's gas key balance without the victim's consent. Because `action_delete_gas_key` hard-aborts with `GasKeyBalanceTooHigh` when `balance > MAX_BALANCE_TO_BURN` (1 NEAR), and `action_delete_account` applies the same threshold across all gas keys, an attacker can permanently prevent the victim from deleting a targeted gas key or their entire account until the victim manually drains the balance via `WithdrawFromGasKey` — which itself requires a full-access key. Victims who hold only function-call keys cannot issue `WithdrawFromGasKey` and are permanently locked.

### Finding Description

`check_actor_permissions` in `runtime/runtime/src/actions.rs` is the single gate that enforces "only the account owner may mutate privileged account state." It correctly requires `actor_id == account_id` for `DeployContract`, `Stake`, `AddKey`, `DeleteKey`, `DeployGlobalContract`, `UseGlobalContract`, `WithdrawFromGasKey`, and `DeleteAccount`. However, `TransferToGasKey` is silently grouped with the unrestricted arm:

```rust
Action::CreateAccount(_)
| Action::FunctionCall(_)
| Action::Transfer(_)
| Action::TransferToGasKey(_) => (),   // ← no actor check
``` [1](#0-0) 

`WithdrawFromGasKey` — the symmetric operation — is explicitly guarded:

```rust
Action::WithdrawFromGasKey(_) => {
    if actor_id != account_id {
        return Err(ActionErrorKind::ActorNoPermission { … }.into());
    }
}
``` [2](#0-1) 

The `promise_batch_action_transfer_to_gas_key` host function is exposed to all contracts: [3](#0-2) 

When a contract calls this host function targeting a foreign account, the VM deducts the amount from the contract's own balance (`deduct_balance`) and appends a `TransferToGasKey` action to a receipt whose `predecessor_id` is the contract and `receiver_id` is the victim. On the victim's shard, `apply_action` calls `check_actor_permissions` — which passes — and then `action_transfer_to_gas_key` unconditionally adds the deposit to `GasKeyInfo.balance`: [4](#0-3) 

The deletion guard that creates the lock:

```rust
if balance > MAX_BALANCE_TO_BURN {
    result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh { … }.into());
    return Ok(());
}
``` [5](#0-4) 

`action_delete_account` applies the same threshold across all gas keys on the account. [6](#0-5) 

### Impact Explanation

An unprivileged attacker contract can:

1. Enumerate a victim's gas key public keys (publicly visible on-chain).
2. Call `promise_batch_action_transfer_to_gas_key(victim_account, gas_key_pk, >1 NEAR)`.
3. The resulting receipt executes on the victim's shard; `check_actor_permissions` passes; `GasKeyInfo.balance` is incremented beyond `MAX_BALANCE_TO_BURN`.
4. Every subsequent `DeleteKey` or `DeleteAccount` call by the victim fails with `GasKeyBalanceTooHigh`.

Recovery requires the victim to issue a `WithdrawFromGasKey` transaction, which is not a `FunctionCall` action and therefore requires a full-access key. Victims who hold only function-call keys (a valid NEAR account configuration) cannot issue this transaction and are permanently unable to delete the targeted gas key or their account. The attacker can repeat the funding to re-lock the key after any partial withdrawal.

The corrupted value is `GasKeyInfo.balance` on the victim's access key trie entry, raised above `MAX_BALANCE_TO_BURN` (1 × 10^24 yoctoNEAR) without the account owner's authorization.

### Likelihood Explanation

- Trigger requires only a deployed contract and > 1 NEAR per targeted gas key — no privileged access.
- Gas key public keys are readable via any RPC `view_access_key_list` call.
- The `promise_batch_action_transfer_to_gas_key` host function is enabled for all contracts under the `GasKeys` feature (protocol version 85, the current stable baseline).
- The attack is cheap relative to the damage: 1–2 NEAR per locked key, and the attacker can re-lock after each victim withdrawal.

### Recommendation

Add `TransferToGasKey` to the actor-permission guard in `check_actor_permissions`, mirroring `WithdrawFromGasKey`:

```rust
Action::WithdrawFromGasKey(_) | Action::TransferToGasKey(_) => {
    if actor_id != account_id {
        return Err(ActionErrorKind::ActorNoPermission {
            account_id: account_id.clone(),
            actor_id: actor_id.clone(),
        }.into());
    }
}
``` [7](#0-6) 

If cross-contract gas key funding is an intentional design goal, the `MAX_BALANCE_TO_BURN` deletion guard must be changed to allow deletion of over-funded keys (e.g., by burning only up to the cap and refunding the remainder to the beneficiary), so that unsolicited funding cannot permanently block deletion.

### Proof of Concept

```rust
// Attacker contract (deployed at attacker.near)
pub fn lock_victim_gas_key(
    victim: AccountId,
    gas_key_pk: PublicKey,
) {
    // Attach > 1 NEAR as deposit to this call
    let promise = env::promise_batch_create(&victim);
    env::promise_batch_action_transfer_to_gas_key(
        promise,
        &gas_key_pk,
        NearToken::from_near(2),   // > MAX_BALANCE_TO_BURN
    );
}
```

After this cross-contract call executes:

```
// victim.near attempts to delete the gas key:
near delete-key victim.near <gas_key_pk>
// → ActionError: GasKeyBalanceTooHigh { account_id: "victim.near", public_key: <gas_key_pk> }

// victim.near attempts to delete their account:
near delete-account victim.near beneficiary.near
// → ActionError: GasKeyBalanceTooHigh { account_id: "victim.near", public_key: <gas_key_pk> }
```

The victim is blocked until they can issue a `WithdrawFromGasKey` transaction signed by a full-access key. If no full-access key exists on the account, the lock is permanent.

### Citations

**File:** runtime/runtime/src/actions.rs (L343-411)
```rust
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

/// Returns the storage usage for the contract code with the given `code_hash` and deployed to the
/// given `account_id`. If no contract was deployed to the account, returns `0`.
///
/// The code-length is obtained without reading the code but from the value-ref in the trie leaf node.
fn get_code_len_or_default(
    state_update: &TrieUpdate,
    account_id: AccountId,
    code_hash: CryptoHash,
) -> Result<StorageUsage, StorageError> {
    let code_len = state_update.get_code_len(account_id, code_hash)?;
    debug_assert!(
        code_len.is_some() || code_hash == CryptoHash::default(),
        "Non-default code hash for account with no contract deployed: {:?}",
        code_hash
    );
    Ok(code_len.unwrap_or_default().try_into().unwrap())
}

fn get_contract_storage_usage(
    state_update: &TrieUpdate,
    account_id: &AccountId,
    account: &Account,
) -> Result<StorageUsage, StorageError> {
    Ok(match account.contract().as_ref() {
        AccountContract::None => 0,
        AccountContract::Local(code_hash) => {
            get_code_len_or_default(state_update, account_id.clone(), *code_hash)?
        }
        AccountContract::Global(_) | AccountContract::GlobalByAccount(_) => {
            account.contract().identifier_storage_usage()
        }
    })
}

/// Clears the contract storage usage based on type for an account.
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

**File:** runtime/near-vm-runner/src/imports.rs (L268-273)
```rust
    #[gas_key_host_fns] promise_batch_action_transfer_to_gas_key<[
        promise_index: u64,
        public_key_len: u64,
        public_key_ptr: u64,
        amount_ptr: u64
    ] -> []>,
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
