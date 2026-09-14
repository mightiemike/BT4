## Analysis

The reported nearcore analog is a state-cleanup gap in `remove_account` that mirrors the `MagnetTurnPlanets` bug class: a table indexed by an account/entity key is not cleared when that entity is "reset" (deleted), so stale entries silently persist and get processed later against a different (recreated) instance of that entity. [1](#0-0) 

`remove_account`, called from `action_delete_account`, only removes the `Account`, `ContractCode`, `AccessKey`/gas-key, and `ContractData` trie entries for the deleted account — it never touches `PostponedReceipt`, `PostponedReceiptId`, `PendingDataCount`, `ReceivedData`, `PromiseYieldReceipt`, or `PromiseYieldStatus` entries keyed by that same `account_id`. [2](#0-1) 

Meanwhile, `process_receipt`'s `Data` branch resolves and executes postponed receipts purely by trie lookups on `(receiver_id, data_id)`/`(receiver_id, receipt_id)` — with no check that the receiving account still exists (or is the "same" account) at the time the awaited data finally arrives: [3](#0-2) 

### Title
Stale postponed/promise-yield receipt state survives `DeleteAccount` and executes against a recreated account - (File: `core/store/src/utils/mod.rs`, `runtime/runtime/src/lib.rs`)

### Summary
`remove_account` (invoked by `action_delete_account`) does not clear the account-scoped receipt-matching tables `PostponedReceipt`, `PostponedReceiptId`, `PendingDataCount`, `ReceivedData`, `PromiseYieldReceipt`, and `PromiseYieldStatus`. These are keyed only by `account_id` (plus `data_id`/`receipt_id`), with no generation counter tying them to the specific account instance that created them. When an account with an outstanding cross-contract callback (input data not yet received) is deleted and later recreated under the same `AccountId`, the original stale `PostponedReceipt` remains fully addressable and will be executed once the matching `DataReceipt`/`PromiseResume` arrives — but now against the freshly recreated account instead of failing cleanly.

### Finding Description
- `action_delete_account` (`runtime/runtime/src/actions.rs:330-406`) removes the `Account` record and calls `remove_account`, which is scoped to `Account`, `ContractCode`, access keys/gas keys, and `ContractData` only: [4](#0-3) 
- The receipt-matching state (`PostponedReceipt`, `PostponedReceiptId`, `PendingDataCount`, `ReceivedData`) is stored under `TrieKey`s keyed purely by `receiver_id`/`account_id` and `data_id`/`receipt_id`: [5](#0-4) 
- None of these keys are removed by `remove_account`, so if account `A` had an in-flight cross-contract call (postponed action receipt awaiting a `DataReceipt`) at the moment it self-deletes (or is deleted by its predecessor), the `PostponedReceipt`/`PostponedReceiptId`/`PendingDataCount` entries for `A` remain in the trie indefinitely.
- If `A` is later recreated (via `CreateAccount`) before the pending `DataReceipt` arrives, the trie state for `A` again resolves: `has_received_data`/`PostponedReceiptId` lookups in `process_action_receipt`/`process_receipt` are unconditional on account existence — they never check whether the account backing this state is the "same" account that created the postponed receipt: [3](#0-2) 
- When the delayed `DataReceipt` finally arrives, `pending_data_count` reaches 0, the stale postponed receipt is fetched and passed straight into `apply_action_receipt`, executing the old receipt's actions (which can include `FunctionCall`, `Transfer`, `AddKey`, `DeleteKey`, `Stake`, etc.) against the account that now exists — i.e., the recreated account, not the one that originally scheduled the callback.
- The identical gap applies to `PromiseYield`/`PromiseResume`: `PromiseYieldReceipt`/`PromiseYieldStatus` entries and the corresponding global `PromiseYieldTimeout` queue entries reference `(account_id, data_id)` and are resolved without any account-instance check in `resolve_promise_yield_timeouts` and the `PromiseResume` branch of `process_receipt`: [6](#0-5) 

This is the direct analog of the reported bug: `remove_account` is the "reset" function, and the postponed/promise-yield tables are the un-cleared `MagnetTurnPlanets`-equivalent tables — later, unrelated activity (a new account creation, followed by a stale receipt arrival) causes long-dead state to be reinterpreted as valid and executed.

### Impact Explanation
A stale postponed receipt executing against a recreated account is an invalid state transition: the receipt's predecessor, actions, and attached deposit/gas were authorized under the assumption they'd run against account `A`'s state as it existed before deletion (e.g., specific access keys, specific contract code/state). Executing it against the new incarnation of `A` means actions such as `FunctionCall`, `Transfer`, `AddKey`, or `DeleteKey` run with a predecessor/authorization context the new account owner never intended to interact with, and with a deposit/refund flow whose accounting assumed the original account timeline. This can result in unauthorized state changes or fund transfers on the recreated account, and — since one honest node could differ from another only in timing of when the account gets recreated relative to receipt arrival within protocol-consistent execution (all nodes replay identically, so this is deterministic, but the *semantic* bug is unauthorized value movement/state mutation on an account whose owner did not create the pending obligation).

### Likelihood Explanation
Requires: (1) an account receiving a self-`DeleteAccount` (or being deleted by an authorized actor) while it still has an outstanding cross-contract-call postponed receipt or promise-yield awaiting external data — an ordinary usage pattern for any dApp with async callbacks; and (2) the same `AccountId` being recreated before the pending data/resume arrives, which any party can trigger by simply calling `CreateAccount` for that id once it's vacant (implicit accounts can even be recreated by anyone transferring to them). No privileged access is required beyond normal transaction/receipt submission.

### Recommendation
When `remove_account` deletes an account, also delete all account-scoped receipt-matching state: iterate and remove `PostponedReceipt`, `PostponedReceiptId`, `PendingDataCount`, and `ReceivedData` entries under that `account_id`'s prefix, and remove/clean up `PromiseYieldReceipt`/`PromiseYieldStatus` entries plus any corresponding `PromiseYieldTimeout` queue entries referencing the deleted account (or otherwise dequeue/invalidate them on account deletion so they cannot be actioned after the account is gone/recreated).

### Proof of Concept
Not directly executable without a live nearcore harness, but the code path is:
1. Account `A` calls a contract on `B` and registers a callback on itself with an `input_data_id` (`output_data_receivers`), creating a `PostponedReceipt`/`PostponedReceiptId`/`PendingDataCount` for `A` while awaiting `B`'s `DataReceipt` — see `process_action_receipt`: [7](#0-6) 
2. Before `B`'s `DataReceipt` reaches `A`, `A` submits a `DeleteAccount` receipt; `action_delete_account` → `remove_account` clears `Account`/keys/code/data but leaves the postponed-receipt trio in place: [4](#0-3) 
3. Anyone recreates account `A` (e.g., a `CreateAccount` action / implicit-account transfer).
4. `B`'s delayed `DataReceipt` finally arrives at `A`; `process_receipt`'s `Data` branch finds the stale `PostponedReceiptId`, decrements `PendingDataCount` to 0, fetches the old `PostponedReceipt`, and executes it via `apply_action_receipt` against the newly (re)created `A`: [8](#0-7)

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

**File:** runtime/runtime/src/actions.rs (L380-404)
```rust
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
```

**File:** runtime/runtime/src/lib.rs (L1449-1509)
```rust
                // Check if there is already a receipt that was postponed and was awaiting for the
                // given data_id.
                // If we don't have a postponed receipt yet, we don't need to do anything for now.
                if let Some(receipt_id) = get(
                    state_update,
                    &TrieKey::PostponedReceiptId {
                        receiver_id: account_id.clone(),
                        data_id: data_receipt.data_id,
                    },
                )? {
                    // There is already a receipt that is awaiting for the just received data.
                    // Removing this pending data_id for the receipt from the state.
                    state_update.remove(TrieKey::PostponedReceiptId {
                        receiver_id: account_id.clone(),
                        data_id: data_receipt.data_id,
                    });
                    // Checking how many input data items is pending for the receipt.
                    let pending_data_count: u32 = get(
                        state_update,
                        &TrieKey::PendingDataCount { receiver_id: account_id.clone(), receipt_id },
                    )?
                    .ok_or_else(|| {
                        StorageError::StorageInconsistentState(
                            "pending data count should be in the state".to_string(),
                        )
                    })?;
                    if pending_data_count == 1 {
                        // It was the last input data pending for this receipt. We'll cleanup
                        // some receipt related fields from the state and execute the receipt.

                        // Removing pending data count from the state.
                        state_update.remove(TrieKey::PendingDataCount {
                            receiver_id: account_id.clone(),
                            receipt_id,
                        });
                        // Fetching the receipt itself.
                        let ready_receipt =
                            get_postponed_receipt(state_update, account_id, receipt_id)?
                                .ok_or_else(|| {
                                    StorageError::StorageInconsistentState(
                                        "pending receipt should be in the state".to_string(),
                                    )
                                })?;
                        // Removing the receipt from the state.
                        remove_postponed_receipt(state_update, account_id, receipt_id);
                        // Executing the receipt. It will read all the input data and clean it up
                        // from the state.
                        return self
                            .apply_action_receipt(
                                state_update,
                                apply_state,
                                pipeline_manager,
                                &ready_receipt,
                                receipt_sink,
                                instant_receipts,
                                validator_proposals,
                                stats,
                                epoch_info_provider,
                                receipt_to_tx,
                            )
                            .map(Some);
```

**File:** runtime/runtime/src/lib.rs (L1662-1709)
```rust
        let mut pending_data_count: u32 = 0;
        for data_id in action_receipt.input_data_ids() {
            if !has_received_data(state_update, account_id, *data_id)? {
                pending_data_count += 1;
                // The data for a given data_id is not available, so we save a link to this
                // receipt_id for the pending data_id into the state.
                set(
                    state_update,
                    TrieKey::PostponedReceiptId {
                        receiver_id: account_id.clone(),
                        data_id: *data_id,
                    },
                    receipt.receipt_id(),
                )
            }
        }

        if pending_data_count == 0 {
            // All input data is available. Executing the receipt. It will cleanup
            // input data from the state.
            return self
                .apply_action_receipt(
                    state_update,
                    apply_state,
                    pipeline_manager,
                    receipt,
                    receipt_sink,
                    instant_receipts,
                    validator_proposals,
                    stats,
                    epoch_info_provider,
                    receipt_to_tx,
                )
                .map(Some);
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

**File:** runtime/runtime/src/lib.rs (L3134-3155)
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
```

**File:** core/primitives/src/trie_key.rs (L192-219)
```rust
    /// Used to store `primitives::receipt::ReceivedData` struct for a given receiver's `AccountId`
    /// of `DataReceipt` and a given `data_id` (the unique identifier for the data).
    /// NOTE: This is one of the input data for some action receipt.
    /// The action receipt might be still not be received or requires more pending input data.
    ReceivedData {
        receiver_id: AccountId,
        data_id: CryptoHash,
    } = col::RECEIVED_DATA,
    /// Used to store receipt ID `primitives::hash::CryptoHash` for a given receiver's `AccountId`
    /// of the receipt and a given `data_id` (the unique identifier for the required input data).
    /// NOTE: This receipt ID indicates the postponed receipt. We store `receipt_id` for performance
    /// purposes to avoid deserializing the entire receipt.
    PostponedReceiptId {
        receiver_id: AccountId,
        data_id: CryptoHash,
    } = col::POSTPONED_RECEIPT_ID,
    /// Used to store the number of still missing input data `u32` for a given receiver's
    /// `AccountId` and a given `receipt_id` of the receipt.
    PendingDataCount {
        receiver_id: AccountId,
        receipt_id: CryptoHash,
    } = col::PENDING_DATA_COUNT,
    /// Used to store the postponed receipt `primitives::receipt::Receipt` for a given receiver's
    /// `AccountId` and a given `receipt_id` of the receipt.
    PostponedReceipt {
        receiver_id: AccountId,
        receipt_id: CryptoHash,
    } = col::POSTPONED_RECEIPT,
```
