### Title
`Action::TransferToGasKey` Missing Actor-Permission Check Allows Any Contract to Manipulate Any Account's Gas Key Balance — (`File: runtime/runtime/src/actions.rs`)

---

### Summary

`check_actor_permissions` enforces `actor_id == account_id` for `WithdrawFromGasKey`, `AddKey`, `DeleteKey`, `DeployContract`, and other sensitive actions, but **omits this check for `TransferToGasKey`**. Because `TransferToGasKey` is callable from contracts via the `promise_batch_action_transfer_to_gas_key` host function, any unprivileged contract can create a cross-contract receipt targeting a victim account and fund that account's gas key with an arbitrary deposit — without the victim's consent.

---

### Finding Description

In `check_actor_permissions` (`runtime/runtime/src/actions.rs:711`), the match arm for `WithdrawFromGasKey` enforces `actor_id == account_id`:

```rust
Action::WithdrawFromGasKey(_) => {
    if actor_id != account_id {
        return Err(ActionErrorKind::ActorNoPermission { ... }.into());
    }
}
```

But `TransferToGasKey` is placed in the unchecked arm:

```rust
Action::CreateAccount(_)
| Action::FunctionCall(_)
| Action::Transfer(_)
| Action::TransferToGasKey(_) => (),   // ← no actor check
``` [1](#0-0) 

`action_transfer_to_gas_key` (`runtime/runtime/src/access_keys.rs:257`) looks up the gas key under `account_id` (the receipt's `receiver_id`) and unconditionally adds `action.deposit` to `gas_key_info.balance`: [2](#0-1) 

The host function `append_action_transfer_to_gas_key` (`runtime/runtime/src/receipt_manager.rs:424`) allows any executing contract to attach this action to a receipt targeting an arbitrary `account_id`: [3](#0-2) 

`apply_action` calls `check_actor_permissions` before dispatching, but since `TransferToGasKey` passes the check unconditionally, the action executes on the victim's account with the attacker as `actor_id`: [4](#0-3) 

---

### Impact Explanation

**Gas key deletion DoS.** `delete_gas_key` (`runtime/runtime/src/access_keys.rs:103`) rejects deletion when `gas_key_info.balance > MAX_BALANCE_TO_BURN` (1 NEAR): [5](#0-4) 

An attacker can fund a victim's gas key with just over 1 NEAR, causing every subsequent `DeleteKey` action on that key to fail with `GasKeyBalanceTooHigh`. The same threshold applies to `action_delete_account`, which sums all gas-key balances: [6](#0-5) 

This prevents the victim from deleting their account until they first issue a `WithdrawFromGasKey` transaction to drain the excess — an extra forced on-chain step the victim did not authorize.

**Unauthorized balance manipulation.** The attacker directly mutates the `GasKeyInfo.balance` field of a gas key they do not own, violating the invariant that only the key's owning account may alter its gas-key balance.

---

### Likelihood Explanation

- Reachable by any unprivileged user who can deploy a contract and call `promise_batch_action_transfer_to_gas_key` targeting a victim's account.
- No privileged role, validator access, or key compromise is required.
- The `GasKeys` feature is enabled at protocol version 85 (`version.rs:562`), which is within `MIN_SUPPORTED_PROTOCOL_VERSION = 83`.
- The attacker spends their own funds, but the cost to push a gas key balance above 1 NEAR is low relative to the disruption caused.

---

### Recommendation

Add `TransferToGasKey` to the `actor_id == account_id` enforcement arm in `check_actor_permissions`, mirroring the treatment of `WithdrawFromGasKey`:

```rust
Action::WithdrawFromGasKey(_)
| Action::TransferToGasKey(_) => {   // ← add here
    if actor_id != account_id {
        return Err(ActionErrorKind::ActorNoPermission {
            account_id: account_id.clone(),
            actor_id: actor_id.clone(),
        }.into());
    }
}
``` [7](#0-6) 

---

### Proof of Concept

1. Alice (`alice.near`) adds a gas key `GK` with `num_nonces = 1` and zero balance.
2. Attacker deploys `attacker.near` with a contract that calls:
   ```
   promise_batch_create("alice.near")
   promise_batch_action_transfer_to_gas_key(promise, GK_pubkey, 1_100_000_000_000_000_000_000_000)  // 1.1 NEAR
   ```
3. The receipt executes on `alice.near`'s shard. `check_actor_permissions` is called with `actor_id = attacker.near`, `account_id = alice.near`. Because `TransferToGasKey` is in the unchecked arm, no error is returned.
4. `action_transfer_to_gas_key` sets `GK.balance = 1.1 NEAR`.
5. Alice submits `Action::DeleteKey { public_key: GK_pubkey }`. The runtime calls `delete_gas_key`, which checks `1.1 NEAR > MAX_BALANCE_TO_BURN (1 NEAR)` and returns `GasKeyBalanceTooHigh`. Alice's `DeleteKey` transaction fails.
6. Alice must first submit `Action::WithdrawFromGasKey { public_key: GK_pubkey, amount: 0.2 NEAR }` to bring the balance below 1 NEAR before she can delete the key — an unauthorized forced action imposed by the attacker.

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

**File:** runtime/runtime/src/actions.rs (L717-756)
```rust
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

**File:** runtime/runtime/src/lib.rs (L563-567)
```rust
        // Permission validation
        if let Err(e) = check_actor_permissions(action, account, actor_id, account_id) {
            result.result = Err(e);
            return Ok(result);
        }
```
