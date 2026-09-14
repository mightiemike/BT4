## Analysis

I found a valid analog. The CVE-2021-32689 bug class is **identity/identifier reuse causing unauthorized access to data or messages that belonged to a previous, unrelated holder of that identifier**. The nearcore analog is in how `AccountId` (Nextcloud username-equivalent) reuse interacts with the postponed-receipt / received-data cross-contract-call machinery, which is keyed purely by `account_id` (`receiver_id`) with no binding to *which incarnation* of that account created the pending state.

`remove_account`, called from `action_delete_account`, deletes the `Account` record, `ContractCode`, all `AccessKey`/gas-key rows, and `ContractData`, but never touches `TrieKey::PostponedReceipt`, `TrieKey::PostponedReceiptId`, `TrieKey::PendingDataCount`, or `TrieKey::ReceivedData` rows for that same `account_id`: [1](#0-0) 

`action_delete_account` itself only checks account storage-usage size and gas-key burn limits before deleting — it performs no check for outstanding postponed receipts or pending data counts for the account being deleted: [2](#0-1) 

Separately, `process_receipt`'s data-receipt handling path looks up `TrieKey::PostponedReceiptId{receiver_id, data_id}` purely by account id string and, once the pending-data counter reaches zero, fetches and executes the postponed `ActionReceipt` directly via `apply_action_receipt`, without re-verifying that the account currently occupying `receiver_id` is the same incarnation that created the postponed receipt: [3](#0-2) 

A named (sub-)account whose `receiver_id` string can be reused (delete then recreate under the same parent, per `action_create_account`'s sub-account rule) is exactly the "username reuse" scenario: [4](#0-3) 

### Title
Postponed cross-contract-call receipts and received data are not purged on `DeleteAccount`, letting a recreated account with the same id receive/execute state addressed to the deleted predecessor - (File: `runtime/runtime/src/actions.rs`)

### Summary
`action_delete_account` removes the `Account`, its code, access keys, gas keys, and contract data, but leaves any `PostponedReceipt`, `PostponedReceiptId`, `PendingDataCount`, and `ReceivedData` trie rows keyed by that `account_id` untouched. If the same `account_id` string is later reused by an unrelated account (a routine, permitted operation for sub-accounts), a stale `DataReceipt` that finally arrives for the old, deleted incarnation will be matched against the new account's identity and cause the runtime to execute the old postponed `ActionReceipt` against the new account's live state.

### Finding Description
`remove_account` (`core/store/src/utils/mod.rs:505-575`) is the sole cleanup routine `action_delete_account` calls. It explicitly enumerates and removes `Account`, `ContractCode`, `AccessKey`/gas-key rows, and `ContractData`, but has no logic touching the `RECEIVED_DATA` (col 3), `POSTPONED_RECEIPT_ID` (col 4), `PENDING_DATA_COUNT` (col 5), or `POSTPONED_RECEIPT` (col 6) trie columns (`core/primitives/src/trie_key.rs:30-41`).

`action_delete_account` (`runtime/runtime/src/actions.rs:330-406`) validates only `MAX_ACCOUNT_DELETION_STORAGE_USAGE` and the gas-key burn cap before deleting; it never checks whether the account still has an unresolved `PostponedReceiptId`/`PendingDataCount` waiting on a `DataReceipt`.

These postponed-receipt rows exist whenever a contract account issues a cross-contract call and is awaiting the callback's `DataReceipt` (`process_action_receipt`, `runtime/runtime/src/lib.rs:1647-1712`, and the data-arrival matching logic in `process_receipt`, `runtime/runtime/src/lib.rs:1439-1509`). That matching logic keys purely on `(receiver_id, data_id)` — it has no way to distinguish "the same logical account that created this pending call" from "whatever account currently occupies this `account_id` string." Once the `PendingDataCount` reaches zero, `get_postponed_receipt`/`remove_postponed_receipt` fetch the old `Receipt` and hand it to `apply_action_receipt`, which executes it against the **current** account object fetched fresh from state — i.e., the new incarnation of `account_id`.

Because sub-account ids are freely re-creatable (`action_implicit_account_creation_transfer`/`action_create_account`, and `implicit_creation_allowed`, `runtime/runtime/src/actions.rs:928-947`), an attacker can: (1) observe/predict that a contract account is about to self-delete or is deleted by its owner while a cross-contract callback is still outstanding, (2) recreate the same `account_id` as their own account (e.g., `foo.bar.near` deleted, then `bar.near` — possibly the same owner acting maliciously, or anyone entitled to create that sub-account — recreates it), and (3) receive execution of the stale postponed receipt's actions against their own newly-created account once the delayed `DataReceipt` finally lands. This is the direct analog of CVE-2021-32689: reusing an identifier grants access to data/execution state that was never intended for the new holder.

### Impact Explanation
This can produce receipt loss/duplication and unauthorized state transitions: a callback receipt intended to finalize logic for the deleted contract (e.g., crediting balances, resolving an escrow/auction, or writing accounting state) instead executes against an attacker-controlled account and its (different or absent) contract code/state, since the account object, code, and storage are re-resolved fresh at execution time. Depending on the actions embedded in the postponed receipt (e.g. `Transfer`, `FunctionCall` with the original signer's authority context still baked into the `ActionReceipt`), this allows value or execution effects meant for the deleted account's logic to instead land on/benefit whoever now controls the reused `account_id`, and it causes stored cross-shard/cross-contract protocol state (`PostponedReceipt`, `ReceivedData`) to leak across unrelated account identities — a correctness violation of the receipt-delivery guarantee described in `docs/RuntimeSpec/Receipts.md`.

### Likelihood Explanation
Reachable by an unprivileged actor: deleting and recreating a sub-account under one's own control (or timing recreation of an account whose deletion is publicly observable via chain data) requires only ordinary `DeleteAccount`/`CreateAccount` transactions, no validator or node-level privilege. The precondition — a pending cross-contract call awaiting a `DataReceipt` at deletion time — is a routine, common contract pattern (any `Promise::then` callback), making the window achievable, especially across shard/cross-contract delays where the response's `DataReceipt` may legitimately take multiple blocks to arrive.

### Recommendation
When deleting an account, purge (or fail deletion for) any outstanding `PostponedReceipt`, `PostponedReceiptId`, `PendingDataCount`, and `ReceivedData` entries associated with that `account_id`, mirroring how `remove_account` already purges access keys and contract data. Alternatively, bind postponed-receipt/received-data trie keys to an account "epoch"/incarnation identifier (incremented on each `CreateAccount`/implicit-creation event) so that data delivered after a delete+recreate cycle can never be matched against a different incarnation's pending state.

### Proof of Concept
1. Deploy contract at `child.alice.near`; have it call another contract and await the callback (`Promise::then`), creating a `PostponedReceipt`/`PendingDataCount`/`PostponedReceiptId` row keyed by `child.alice.near`.
2. Before the callback `DataReceipt` arrives, submit `DeleteAccount{beneficiary_id: alice.near}` from `child.alice.near` (`runtime/runtime/src/actions.rs:330`). Confirm via `remove_account` (`core/store/src/utils/mod.rs:505`) that only `Account`/`ContractCode`/`AccessKey`/`ContractData` rows are removed — the postponed-receipt state remains.
3. From `alice.near`, submit `CreateAccount` for `child.alice.near` again (now owned/controlled independently), deploying different code or simply leaving it with a full-access key of the recreator's choosing.
4. Let the original callback's `DataReceipt` finally be delivered (it was already in flight cross-shard before step 2). Observe `process_receipt` (`runtime/runtime/src/lib.rs:1452-1509`) matches it against the still-present `PostponedReceiptId{receiver_id: child.alice.near, data_id}`, fetches the old `PostponedReceipt`, and executes it via `apply_action_receipt` against the newly created (step 3) account instead of failing or being dropped.

### Citations

**File:** core/store/src/utils/mod.rs (L504-553)
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
```

**File:** runtime/runtime/src/actions.rs (L211-220)
```rust
/// Can only be used for implicit accounts.
///
/// The account is created without claiming `actor_id`, which stays the receipt's
/// predecessor. A `0u` id can be created by a transfer inside a batch (see
/// [`implicit_creation_allowed`]), and claiming it would hand the
/// rest of that batch the new account's own authority: a relayer sending
/// `[Transfer, UniversalStateInit, AddKey]` would install a key the id does not
/// commit to, and one ending in `DeleteAccount` would take the balance. For the
/// other implicit kinds the transfer is the whole receipt, so there is nothing
/// after it to authorize either way.
```

**File:** runtime/runtime/src/actions.rs (L330-387)
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
```

**File:** runtime/runtime/src/lib.rs (L1439-1509)
```rust
        match receipt.versioned_receipt() {
            VersionedReceiptEnum::Data(data_receipt) => {
                // Received a new data receipt.
                // Saving the data into the state keyed by the data_id.
                set_received_data(
                    state_update,
                    account_id.clone(),
                    data_receipt.data_id,
                    &ReceivedData { data: data_receipt.data.clone() },
                );
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
