### Title
Improper Resource Release on `DeleteAccount`: PromiseYield/PostponedReceipt State Survives Account Deletion, Enabling Stale Cross-Incarnation Receipt Execution - (File: `core/store/src/utils/mod.rs`)

### Summary
`remove_account`, the routine invoked by `action_delete_account` to tear down an account, only removes the `Account`, `ContractCode`, access/gas keys, and `ContractData` entries for the account. [1](#0-0) 
It never removes `PromiseYieldReceipt`, `PromiseYieldStatus`, the `PromiseYieldTimeout` queue entry, `PostponedReceipt`, `PostponedReceiptId`, or `PendingDataCount` rows keyed to that account. This mirrors the CWE-404 pattern in the referenced Vault advisory: a scope (there, a namespace; here, an account) is torn down without revoking/releasing every resource bound to it, leaving state that outlives the scope it belonged to.

### Finding Description
`action_delete_account` calls `remove_account` and then sets `*account = None`, treating the account as fully gone. [2](#0-1) 
`remove_account` itself is explicit about what it cleans up — account, code, access keys, gas-key nonces, contract data — and stops there. [3](#0-2) 

Separately, the runtime keeps several account-scoped queues that are *not* part of this list:
- `PromiseYieldReceipt` / `PromiseYieldStatus`, set when a `PromiseYield` receipt is received and awaits its `PromiseResume` (`set_promise_yield_receipt`, `lib.rs:1552`).
- The `PromiseYieldTimeout` trie-queue entry that will synthesize a timeout `PromiseResume` for that same `(receiver_id, data_id)` pair once `expires_at` is reached (`resolve_promise_yield_timeouts`, `lib.rs:3113-3209`). [4](#0-3) 
- `PostponedReceipt` / `PostponedReceiptId` / `PendingDataCount`, set when an action receipt has unresolved `input_data_ids` (`process_action_receipt`, `lib.rs:1647-1712`). [5](#0-4) 

If an account deletes itself (or is deleted by its beneficiary flow) while it has a pending `PromiseYield` outstanding, the `PromiseYieldReceipt`/`PromiseYieldStatus`/`PromiseYieldTimeout` entries for it are left in the trie untouched by `remove_account`. Because NEAR permits an account name to be recreated after deletion (a parent account can always re-issue the same sub-account id, and a deleted top-level id can in principle be reused), a second incarnation of the same `account_id` can come into existence before the stale timeout fires or before a resume arrives. When the timeout eventually elapses, `resolve_promise_yield_timeouts` finds the leftover `PromiseYieldReceipt` key still present (it was never removed at deletion time) and synthesizes a `PromiseResume` receipt destined for that same account id. [6](#0-5) 
`process_receipt`'s `PromiseResume` branch then unconditionally looks the stale yield receipt back up by `(account_id, data_id)`, and — finding it — executes it via `apply_action_receipt` against whatever account currently occupies that id, i.e. the *new* incarnation, not the one that originally created the yield. [7](#0-6) 

This is the improper-shutdown analog: the "session" (the account and everything scoped to it) was supposed to be fully revoked on `DeleteAccount`, but a category of pending cross-receipt state (yielded promises, postponed receipts and their pending-data counters) survives the teardown and is later replayed against a different logical entity that happens to reuse the same account id.

### Impact Explanation
The stale yield/postponed receipt carries actions (e.g. a `FunctionCall` with an attached deposit, or further `Transfer`/action chains) that were authorized under the *old* incarnation's contract code/state and predecessor context, but get applied to the *new* incarnation's account and possibly its newly deployed contract. Because `apply_action_receipt` re-resolves the account fresh at execution time and `check_actor_permissions`/`check_account_existence` only validate against the account as it exists *now*, a receipt that should have died with the old account instead executes unexpected logic (deposits, function calls, or refunds) against the new account holder — an instance of receipt state outliving its owner and being replayed into a context it was never authorized for. Depending on the resumed receipt's actions this can move value (deposit credited to the wrong/new incarnation), invoke functions on a re-deployed contract with attacker-influenced timing, or, in the postponed-receipt case, cause spurious storage/account errors once the data eventually arrives. This is a resource-lifecycle bug of the same class as the Vault advisory (secrets outliving their owning scope), reachable purely by an ordinary account owner performing yield-create → delete-account → (attacker or same owner) recreate-account, with no privileged/validator access required.

### Likelihood Explanation
Reachable entirely from unprivileged transactions: any account can call `promise_yield_create` (via a contract using the yield/resume host functions), then submit a `DeleteAccount` action for itself, and later recreate the same account id (trivial for sub-accounts, where the parent always controls recreation; possible in principle for reused top-level ids). No cross-validator or network-timing assumptions are needed — the timeout queue and resume delivery are both deterministic, protocol-level mechanisms already exercised by `resolve_promise_yield_timeouts` and the `PromiseResume` receipt path. The main precondition is a nontrivial timing window between deletion and the yield's `expires_at`/resume, which an attacker fully controls since NEAR yield timeouts and deletion are both attacker-timed transactions.

### Recommendation
Extend `remove_account` (or `action_delete_account`) to also purge the account-scoped yield/postponed-receipt bookkeeping before treating the account as gone: iterate and remove `PromiseYieldReceipt`, `PromiseYieldStatus`, and the corresponding `PromiseYieldTimeout` queue entries for the account, and likewise `PostponedReceipt`, `PostponedReceiptId`, and `PendingDataCount` rows tied to it (as is already done for access keys and contract data via prefix iteration in the same function). If eager removal is deemed too state-heavy, at minimum `resolve_promise_yield_timeouts` and the `PromiseResume` branch of `process_receipt` should verify the account existed continuously since the yield was created (e.g. via a generation/nonce check on the account) before replaying the parked receipt, and drop/refund it instead of executing it against a different incarnation.

### Proof of Concept
Conceptual reproduction using only transaction-level actions:
1. Account `a.parent.near` deploys a contract and calls a method that invokes `promise_yield_create`, storing `PromiseYieldReceipt`/`PromiseYieldStatus` keyed to `(a.parent.near, data_id)` and enqueuing a `PromiseYieldTimeout` entry (`lib.rs:1552`, `TrieKey::PromiseYieldTimeout`).
2. In the same or a later block, `a.parent.near` submits `DeleteAccount{beneficiary_id: parent.near}`. `action_delete_account`/`remove_account` deletes the account, its keys and its contract data, but leaves the `PromiseYieldReceipt`/`PromiseYieldStatus`/`PromiseYieldTimeout` rows untouched (`core/store/src/utils/mod.rs:504-575`).
3. `parent.near` (the only entity that can recreate the sub-account) issues `CreateAccount` for `a.parent.near` again and deploys a new/different contract.
4. Once `expires_at` is reached, `resolve_promise_yield_timeouts` finds the leftover `PromiseYieldReceipt` key still present and emits a `PromiseResume` receipt targeting `a.parent.near` (`lib.rs:3150-3172`).
5. `process_receipt`'s `PromiseResume` handling finds the stale yield receipt via `get_promise_yield_receipt`, and calls `apply_action_receipt` on it — executing the *original* incarnation's queued actions against the *newly created* `a.parent.near` account (`lib.rs:1568-1616`).

This sequence can be constructed today purely from RPC-submitted transactions/contract calls (yield-create, delete-account, recreate-account) without any node, validator, or network-level compromise, demonstrating that account-scoped receipt state is not properly revoked on `DeleteAccount`.

### Citations

**File:** core/store/src/utils/mod.rs (L504-575)
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
}
```

**File:** runtime/runtime/src/actions.rs (L387-405)
```rust
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
```

**File:** runtime/runtime/src/lib.rs (L1568-1616)
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

**File:** runtime/runtime/src/lib.rs (L1696-1709)
```rust
        } else {
            // Not all input data is available now.
            // Save the counter for the number of pending input data items into the state.
            set(
                state_update,
                TrieKey::PendingDataCount {
                    receiver_id: account_id.clone(),
                    receipt_id: *receipt.receipt_id(),
                },
                &pending_data_count,
            );
            // Save the receipt itself into the state.
            set_postponed_receipt(state_update, receipt);
        }
```

**File:** runtime/runtime/src/lib.rs (L3134-3172)
```rust
        let queue_entry_key =
            TrieKey::PromiseYieldTimeout { index: promise_yield_indices.first_index };

        let queue_entry =
            get::<PromiseYieldTimeout>(state_update, &queue_entry_key)?.ok_or_else(|| {
                StorageError::StorageInconsistentState(format!(
                    "PromiseYield timeout queue entry #{} should be in the state",
                    promise_yield_indices.first_index
                ))
            })?;

        // Queue entries are ordered by expires_at
        if queue_entry.expires_at > apply_state.block_height {
            break;
        }

        // Check if the yielded promise still needs to be resolved
        let promise_yield_key = TrieKey::PromiseYieldReceipt {
            receiver_id: queue_entry.account_id.clone(),
            data_id: queue_entry.data_id,
        };
        if state_update.contains_key(&promise_yield_key, AccessOptions::DEFAULT)? {
            let new_receipt_id = create_receipt_id_from_receipt_id(
                &queue_entry.data_id,
                apply_state.block_height,
                new_receipt_index,
            );
            new_receipt_index += 1;

            // Create a PromiseResume receipt to resolve the timed-out yield.
            let resume_receipt = Receipt::V0(ReceiptV0 {
                predecessor_id: queue_entry.account_id.clone(),
                receiver_id: queue_entry.account_id.clone(),
                receipt_id: new_receipt_id,
                receipt: ReceiptEnum::PromiseResume(DataReceipt {
                    data_id: queue_entry.data_id,
                    data: None,
                }),
            });
```
