### Title
Stale `PromiseYieldReceipt` Survives Account Deletion, Executes Malicious Callback on Re-created Account — (`core/store/src/utils/mod.rs`, `runtime/runtime/src/lib.rs`)

---

### Summary

`remove_account` iterates and removes access keys, gas-key nonces, and contract data, but it never touches `TrieKey::PromiseYieldReceipt`, `TrieKey::PromiseYieldStatus`, `TrieKey::YieldIdToDataId`, or `TrieKey::DataIdToYieldId` entries. A `PromiseYieldTimeout` entry in the global queue is also never cleaned up. When an account that had an active yielded promise is deleted and later re-created under the same ID, the stale `PromiseYieldReceipt` (containing attacker-chosen callback actions) is still present in the trie. The timeout mechanism fires against the re-created account and executes the old callback — including a `DeleteAccount` action that drains the victim's balance to the attacker.

---

### Finding Description

`remove_account` in `core/store/src/utils/mod.rs` removes:

- `TrieKey::Account`
- `TrieKey::ContractCode`
- All `TrieKey::AccessKey` / `TrieKey::GasKeyNonce` entries (via prefix scan)
- All `TrieKey::ContractData` entries (via prefix scan) [1](#0-0) 

It does **not** remove:

- `TrieKey::PromiseYieldReceipt { receiver_id, data_id }` — the stored callback receipt
- `TrieKey::PromiseYieldStatus { receiver_id, data_id }` — the yield status
- `TrieKey::YieldIdToDataId` / `TrieKey::DataIdToYieldId` — bidirectional mappings [2](#0-1) 

These keys are written by `set_promise_yield_receipt` and `set_promise_yield_status` (called from `process_receipt` in `lib.rs`), which do not update `account.storage_usage()`. Therefore `action_delete_account`'s storage-cap check does not account for them, and the account can be deleted while yield state remains. [3](#0-2) 

The global `PromiseYieldTimeout` queue entry (`TrieKey::PromiseYieldTimeout { index }`) is also never removed on account deletion — it is a global queue keyed by index, not by account.

When the timeout fires in `resolve_promise_yield_timeouts`, it checks only whether `TrieKey::PromiseYieldReceipt` still exists in the trie: [4](#0-3) 

Because the stale receipt was never deleted, this check returns `true`, and a `PromiseResume` receipt is created targeting the re-created account. When processed, `process_receipt` finds the stale `PromiseYieldReceipt` and calls `apply_action_receipt` with it: [5](#0-4) 

The `PromiseYieldReceipt` was created with `predecessor_id = alice.near` (the account that called `promise_yield_create` on itself). When `apply_action_receipt` runs, `actor_id` is initialized to `receipt.predecessor_id() = alice.near`. Since `actor_id == account_id`, the `DeleteAccount` permission check in `check_actor_permissions` passes: [6](#0-5) 

---

### Impact Explanation

An attacker who previously controlled `alice.near` can:

1. Deploy a contract that calls `promise_yield_create` on itself with a callback containing `DeleteAccount { beneficiary_id: attacker.near }`.
2. Delete `alice.near` (the account record, access keys, and contract data are removed; the `PromiseYieldReceipt` and timeout entry remain).
3. Wait for a victim to re-create `alice.near` and deposit funds.
4. When the yield timeout fires, the stale callback executes on the new `alice.near`, deletes it, and transfers its entire balance to `attacker.near`.

This is a direct, unprivileged **fund theft** from the re-created account. The attacker needs no special role after step 2; the protocol itself delivers the malicious receipt.

---

### Likelihood Explanation

Named accounts (e.g., `project.near`, `dao.near`) are commonly re-created after deletion. The attacker only needs to have previously held the account, which is a normal user action. The window between deletion and re-creation can be arbitrarily long (up to the yield timeout, which is configurable by the contract). No validator cooperation or privileged access is required after the initial setup.

---

### Recommendation

`remove_account` must clean up all yield-related trie state for the account being deleted. Specifically:

1. Iterate and remove all `TrieKey::PromiseYieldReceipt` entries under `receiver_id = account_id`.
2. Remove corresponding `TrieKey::PromiseYieldStatus`, `TrieKey::YieldIdToDataId`, and `TrieKey::DataIdToYieldId` entries.
3. Remove or invalidate the corresponding `TrieKey::PromiseYieldTimeout` entries from the global queue (or check at timeout-fire time whether the account still exists and whether the yield receipt's creation epoch matches the current account's creation epoch).

An epoch-stamped account identity (similar to the `ACCESS_KEY_NONCE_RANGE_MULTIPLIER` fix for nonce replay after account re-creation) would also prevent stale receipts from a prior incarnation from executing against a new one. [7](#0-6) 

---

### Proof of Concept

```
Block N:
  alice.near deploys contract C.
  C calls promise_yield_create(receiver=alice.near, callback=[DeleteAccount{beneficiary=attacker.near}], timeout=T).
  → TrieKey::PromiseYieldReceipt{alice.near, data_id_X} written.
  → TrieKey::PromiseYieldTimeout{index=K} written (expires_at = N+T).

Block N+1:
  alice.near sends DeleteAccount{beneficiary=attacker.near}.
  remove_account(alice.near) runs:
    - removes TrieKey::Account{alice.near}
    - removes TrieKey::ContractCode{alice.near}
    - removes all AccessKey entries
    - removes all ContractData entries
    *** TrieKey::PromiseYieldReceipt{alice.near, data_id_X} NOT removed ***
    *** TrieKey::PromiseYieldTimeout{index=K}              NOT removed ***

Block N+2:
  Victim sends 100 NEAR to alice.near → implicit account re-creation.
  alice.near now has balance=100 NEAR, no contract.

Block N+T (timeout fires):
  resolve_promise_yield_timeouts checks:
    contains_key(TrieKey::PromiseYieldReceipt{alice.near, data_id_X}) → TRUE (stale)
  Creates PromiseResume receipt → alice.near.

  process_receipt(PromiseResume):
    get_promise_yield_receipt(alice.near, data_id_X) → old receipt with [DeleteAccount{attacker.near}]
    apply_action_receipt(old_receipt):
      actor_id = old_receipt.predecessor_id = alice.near
      account_id = alice.near
      actor_id == account_id → DeleteAccount permitted
      account.locked == 0 → DeleteAccount permitted
      alice.near balance (100 NEAR) transferred to attacker.near
      alice.near deleted.

Result: attacker.near receives 100 NEAR stolen from victim.
```

### Citations

**File:** core/store/src/utils/mod.rs (L182-316)
```rust
pub fn set_promise_yield_receipt(state_update: &mut TrieUpdate, receipt: &Receipt) {
    match receipt.versioned_receipt() {
        VersionedReceiptEnum::PromiseYield(action_receipt) => {
            assert!(action_receipt.input_data_ids().len() == 1);
            let key = TrieKey::PromiseYieldReceipt {
                receiver_id: receipt.receiver_id().clone(),
                data_id: action_receipt.input_data_ids()[0],
            };
            set(state_update, key, receipt);
        }
        _ => unreachable!("Expected PromiseYield receipt"),
    }
}

pub fn remove_promise_yield_receipt(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) {
    state_update.remove(TrieKey::PromiseYieldReceipt { receiver_id: receiver_id.clone(), data_id });
}

pub fn get_promise_yield_receipt(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) -> Result<Option<Receipt>, StorageError> {
    get(trie, &TrieKey::PromiseYieldReceipt { receiver_id: receiver_id.clone(), data_id })
}

pub fn has_promise_yield_receipt(
    trie: &dyn TrieAccess,
    receiver_id: AccountId,
    data_id: CryptoHash,
) -> Result<bool, StorageError> {
    trie.contains_key(
        &TrieKey::PromiseYieldReceipt { receiver_id, data_id },
        AccessOptions::DEFAULT,
    )
}

pub fn get_promise_yield_status(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) -> Result<Option<PromiseYieldStatus>, StorageError> {
    get(trie, &TrieKey::PromiseYieldStatus { receiver_id: receiver_id.clone(), data_id })
}

pub fn has_promise_yield_status(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) -> Result<bool, StorageError> {
    trie.contains_key(
        &TrieKey::PromiseYieldStatus { receiver_id: receiver_id.clone(), data_id },
        AccessOptions::DEFAULT,
    )
}

pub fn set_promise_yield_status(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    data_id: CryptoHash,
    status: PromiseYieldStatus,
) {
    set(
        state_update,
        TrieKey::PromiseYieldStatus { receiver_id: receiver_id.clone(), data_id },
        &status,
    );
}

pub fn remove_promise_yield_status(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) {
    state_update.remove(TrieKey::PromiseYieldStatus { receiver_id: receiver_id.clone(), data_id });
}

pub fn set_yield_id_mapping(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    yield_id: YieldId,
    data_id: CryptoHash,
) {
    set(
        state_update,
        TrieKey::YieldIdToDataId { receiver_id: receiver_id.clone(), yield_id },
        &data_id,
    );
    set(
        state_update,
        TrieKey::DataIdToYieldId { receiver_id: receiver_id.clone(), data_id },
        &yield_id,
    );
}

pub fn get_data_id_for_yield_id(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    yield_id: YieldId,
) -> Result<Option<CryptoHash>, StorageError> {
    get(trie, &TrieKey::YieldIdToDataId { receiver_id: receiver_id.clone(), yield_id })
}

pub fn get_yield_id_for_data_id(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    data_id: CryptoHash,
) -> Result<Option<YieldId>, StorageError> {
    get(trie, &TrieKey::DataIdToYieldId { receiver_id: receiver_id.clone(), data_id })
}

pub fn has_yield_id_mapping(
    trie: &dyn TrieAccess,
    receiver_id: &AccountId,
    yield_id: YieldId,
) -> Result<bool, StorageError> {
    trie.contains_key(
        &TrieKey::YieldIdToDataId { receiver_id: receiver_id.clone(), yield_id },
        AccessOptions::DEFAULT,
    )
}

pub fn remove_yield_id_mappings(
    state_update: &mut TrieUpdate,
    receiver_id: &AccountId,
    yield_id: YieldId,
    data_id: CryptoHash,
) {
    state_update.remove(TrieKey::YieldIdToDataId { receiver_id: receiver_id.clone(), yield_id });
    state_update.remove(TrieKey::DataIdToYieldId { receiver_id: receiver_id.clone(), data_id });
}
```

**File:** core/store/src/utils/mod.rs (L486-556)
```rust
/// Removes account, code and all access keys and gas keys associated to it.
pub fn remove_account(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
) -> Result<RemoveAccountResult, StorageError> {
    state_update.remove(TrieKey::Account { account_id: account_id.clone() });
    state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });

    let mut gas_key_nonce_count: usize = 0;
    let mut gas_key_nonce_total_key_bytes: usize = 0;

    // Removing access keys and gas key nonces
    let lock = state_update.trie().lock_for_iter();
    let mut keys_to_remove: Vec<TrieKey> = Vec::new();
    for raw_key in state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_access_keys(account_id), &lock)?
    {
        let raw_key = raw_key?;
        let key_handle = trie_key_parsers::parse_key_handle_from_access_key_key(
            &raw_key, account_id,
        )
        .map_err(|_e| {
            StorageError::StorageInconsistentState(
                "Can't parse key handle from raw key for AccessKey".to_string(),
            )
        })?;
        let nonce_index =
            trie_key_parsers::parse_nonce_index_from_gas_key_key(&raw_key, account_id, &key_handle)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse nonce index from raw key for AccessKey".to_string(),
                    )
                })?;
        if let Some(index) = nonce_index {
            gas_key_nonce_count += 1;
            gas_key_nonce_total_key_bytes += raw_key.len();
            keys_to_remove.push(TrieKey::gas_key_nonce(
                account_id.clone(),
                key_handle.clone(),
                index,
            ));
        } else {
            keys_to_remove.push(TrieKey::access_key(account_id.clone(), key_handle.clone()));
        }
    }
    drop(lock);

    for trie_key in keys_to_remove {
        state_update.remove(trie_key);
    }

    // Removing contract data
    let lock = state_update.trie().lock_for_iter();
    let data_keys = state_update
        .locked_iter(&trie_key_parsers::get_raw_prefix_for_contract_data(account_id, &[]), &lock)?
        .map(|raw_key| {
            trie_key_parsers::parse_data_key_from_contract_data_key(&raw_key?, account_id)
                .map_err(|_e| {
                    StorageError::StorageInconsistentState(
                        "Can't parse data key from raw key for ContractData".to_string(),
                    )
                })
                .map(Vec::from)
        })
        .collect::<Result<Vec<_>, _>>()?;
    drop(lock);

    for key in data_keys {
        state_update.remove(TrieKey::ContractData { account_id: account_id.clone(), key });
    }
    Ok(RemoveAccountResult { gas_key_nonce_count, gas_key_nonce_total_key_bytes })
```

**File:** runtime/runtime/src/actions.rs (L333-356)
```rust
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
```

**File:** runtime/runtime/src/actions.rs (L733-748)
```rust
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
```

**File:** runtime/runtime/src/lib.rs (L1447-1495)
```rust
                if let Some(yield_receipt) =
                    get_promise_yield_receipt(state_update, account_id, data_receipt.data_id)?
                {
                    // Remove the receipt from the state
                    remove_promise_yield_receipt(state_update, account_id, data_receipt.data_id);

                    // Clear the PromiseYield status
                    remove_promise_yield_status(state_update, account_id, data_receipt.data_id);

                    // Clean up yield_id <-> data_id mappings if this was created by yield_create_with_id
                    if ProtocolFeature::YieldWithId.enabled(apply_state.current_protocol_version) {
                        if let Some(yield_id) = get_yield_id_for_data_id(
                            state_update,
                            account_id,
                            data_receipt.data_id,
                        )? {
                            remove_yield_id_mappings(
                                state_update,
                                account_id,
                                yield_id,
                                data_receipt.data_id,
                            );
                        }
                    }

                    // Save the data into the state keyed by the data_id
                    set_received_data(
                        state_update,
                        account_id.clone(),
                        data_receipt.data_id,
                        &ReceivedData { data: data_receipt.data.clone() },
                    );

                    // Execute the PromiseYield receipt. It will read the input data and clean it
                    // up from the state.
                    return self
                        .apply_action_receipt(
                            state_update,
                            apply_state,
                            pipeline_manager,
                            &yield_receipt,
                            receipt_sink,
                            instant_receipts,
                            validator_proposals,
                            stats,
                            epoch_info_provider,
                            receipt_to_tx,
                        )
                        .map(Some);
```

**File:** runtime/runtime/src/lib.rs (L2980-2984)
```rust
        let promise_yield_key = TrieKey::PromiseYieldReceipt {
            receiver_id: queue_entry.account_id.clone(),
            data_id: queue_entry.data_id,
        };
        if state_update.contains_key(&promise_yield_key, AccessOptions::DEFAULT)? {
```

**File:** core/primitives-core/src/account.rs (L468-470)
```rust
    /// Nonce for this access key, used for tx nonce generation. When access key is created, nonce
    /// is set to `(block_height - 1) * 1e6` to avoid tx hash collision on access key re-creation.
    /// See <https://github.com/near/nearcore/issues/3779> for more details.
```
