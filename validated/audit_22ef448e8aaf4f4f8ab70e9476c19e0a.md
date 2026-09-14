### Title
Incomplete account deletion leaves postponed-receipt / promise-yield state in the trie, enabling stale receipts to execute against a re-created account - (File: `runtime/runtime/src/actions.rs`, `core/store/src/utils/mod.rs`)

### Summary
`action_delete_account` deletes a NEAR account by calling `remove_account`, which explicitly (and correctly) iterates and removes the account's `Account`, `ContractCode`, `AccessKey`/gas-key-nonce, and `ContractData` trie entries [1](#0-0) . However, several other trie columns are keyed by the same `account_id` — `PostponedReceiptId`, `PendingDataCount`, `PostponedReceipt`, `ReceivedData`, `PromiseYieldReceipt`, `PromiseYieldStatus`, `YieldIdToDataId`, `DataIdToYieldId` [2](#0-1)  — and `remove_account` never touches them. This is the direct Rust analogue of the Solidity report: deleting the "struct" (the account) does not clear the associated mapping-like state that is keyed by the same identifier, so old data survives and can later be picked up by anyone who reuses that key (account_id).

### Finding Description
`action_delete_account` performs only a storage-usage cap check, a gas-key-balance cap check, and (via `check_actor_permissions`) a "not currently staked" check before calling `remove_account` and clearing the account [3](#0-2) [4](#0-3) . There is no check for, and no cleanup of, any outstanding postponed action receipts or yielded promises addressed to that account.

When a `FunctionCall`-driven action receipt has unresolved `input_data_ids`, the runtime persists it under keys derived solely from `account_id` (the receiver) plus a `data_id`/`receipt_id`: `TrieKey::PostponedReceiptId`, `TrieKey::PendingDataCount`, and the `PostponedReceipt` payload itself [5](#0-4) . Similarly, `PromiseYield`/`PromiseResume` bookkeeping (`PromiseYieldReceipt`, `PromiseYieldStatus`, yield-id mappings) is keyed only by `receiver_id`/`account_id` [6](#0-5) [7](#0-6) .

When the missing data eventually arrives (a `DataReceipt`), `process_receipt`'s `Data` branch looks up `PostponedReceiptId`/`PendingDataCount` purely by `account_id`/`data_id`/`receipt_id`, decrements the pending count, and — once it hits zero — fetches and executes the stored `PostponedReceipt` via `apply_action_receipt`, with **no check that the account still exists or is the same account that originally created the dependency** [8](#0-7) . The `PromiseResume` branch behaves the same way for yielded promises [9](#0-8) .

Because NEAR named (sub-)accounts can be deleted and later re-created under the same `account_id` (an already-documented general NEAR risk), the following sequence is possible:
1. Account `X` (a contract) issues/receives an action receipt with two or more `input_data_ids` (e.g. a `Promise::and`/`.then()` join) that does not resolve immediately, causing the receipt to be stored as `PostponedReceipt` keyed to `X`.
2. Before all dependencies resolve, `X` self-deletes via `DeleteAccountAction` (permitted since `X` has no locked stake and its storage usage is under the cap — note storage-usage accounting does not include postponed-receipt bookkeeping, so this is unaffected by the pending state).
3. `X`'s name is re-created (a fresh account, possibly by an unrelated party/owner).
4. The still-outstanding `DataReceipt`(s) for the original dependencies are delivered later. `process_receipt`'s `Data` branch finds the leftover `PostponedReceiptId`/`PendingDataCount` entries under `X`'s key, reconstructs the original `PostponedReceipt`, and executes its actions against the **newly re-created** `X` — actions the new owner never authorized.

### Impact Explanation
This allows arbitrary previously-queued actions (whatever was in the original postponed action receipt — transfers, function calls, access-key/gas-key operations, etc.) to be executed against an account that has since been re-created by a different, unrelated party, without any authorization from the new owner. Depending on the action list this can move balance, mutate contract state, or spawn further receipts on the new account — an unauthorized state transition triggered purely by transaction/receipt timing that any user can arrange for their own account before self-deleting it. It matches the report's underlying vulnerability class: data thought to be erased by "deleting the struct" persists and is later reachable via the reused key.

### Likelihood Explanation
Reaching this requires: (a) constructing a receipt to a self-controlled account with an unresolved multi-dependency promise, (b) submitting a `DeleteAccount` action once the account qualifies (no lock, storage under cap), and (c) the account name being reused afterward. All of these are actions available to a normal, unprivileged account holder/contract via ordinary transactions and cross-contract calls — no validator, network, or operator privilege is needed. The scenario requires specific timing/setup, so likelihood is moderate rather than trivial, but it is fully reachable from a single signer's transactions and receipts.

### Recommendation
`remove_account` (or `action_delete_account`) should also enumerate and remove all `PostponedReceiptId`, `PendingDataCount`, `PostponedReceipt`, `ReceivedData`, `PromiseYieldReceipt`, `PromiseYieldStatus`, `YieldIdToDataId`, and `DataIdToYieldId` entries for the account being deleted (mirroring how access keys and contract data are already iterated and explicitly removed), or alternatively reject `DeleteAccountAction` outright while any such pending entries exist for the account.

### Proof of Concept
Conceptual reproduction using existing test scaffolding (`runtime/runtime/src/actions_test_utils.rs`, `runtime/runtime/src/tests/apply.rs`, `test-loop-tests/src/tests/yield_resume.rs`, `test-loop-tests/src/tests/create_delete_account.rs`):
1. Deploy a contract to `child.alice.near` that issues a `Promise` with two `.then()`/join dependencies (two `input_data_ids`) targeting itself, so `process_action_receipt` stores `PostponedReceiptId`, `PendingDataCount`, and `PostponedReceipt` keyed to `child.alice.near` [5](#0-4) .
2. Before both dependencies resolve, send a `DeleteAccount` receipt for `child.alice.near` (analogous to the existing `test_function_call_after_same_chunk_delete_recreate_resolves_fresh_code` test pattern) [10](#0-9) ; confirm via `remove_account` that only `Account`/`ContractCode`/`AccessKey`/`ContractData` are cleared [11](#0-10) .
3. Re-create `child.alice.near` as a fresh account (`CreateAccount` action from `alice.near`).
4. Deliver the two outstanding `DataReceipt`s. Observe that `process_receipt`'s `Data` branch still finds the stale `PostponedReceiptId`/`PendingDataCount` under `child.alice.near` and executes the original `PostponedReceipt`'s actions against the freshly created account [8](#0-7) , confirming actions execute despite the account having been deleted and re-created in between.

### Citations

**File:** core/store/src/utils/mod.rs (L200-212)
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
```

**File:** core/store/src/utils/mod.rs (L281-297)
```rust
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
```

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

**File:** core/primitives/src/trie_key.rs (L87-102)
```rust
    /// All columns except those used for the delayed receipts queue, the yielded promises
    /// queue, and the outgoing receipts buffer, which are global state for the shard.
    pub const COLUMNS_WITH_ACCOUNT_ID_IN_KEY: [(u8, &str); 12] = [
        (ACCOUNT, "Account"),
        (CONTRACT_CODE, "ContractCode"),
        (ACCESS_KEY, "AccessKey"),
        (RECEIVED_DATA, "ReceivedData"),
        (POSTPONED_RECEIPT_ID, "PostponedReceiptId"),
        (PENDING_DATA_COUNT, "PendingDataCount"),
        (POSTPONED_RECEIPT, "PostponedReceipt"),
        (CONTRACT_DATA, "ContractData"),
        (PROMISE_YIELD_RECEIPT, "PromiseYieldReceipt"),
        (PROMISE_YIELD_STATUS, "PromiseYieldStatus"),
        (YIELD_ID_TO_DATA_ID, "YieldIdToDataId"),
        (DATA_ID_TO_YIELD_ID, "DataIdToYieldId"),
    ];
```

**File:** runtime/runtime/src/actions.rs (L330-406)
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

**File:** runtime/runtime/src/actions.rs (L777-791)
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
```

**File:** runtime/runtime/src/lib.rs (L1440-1509)
```rust
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

**File:** runtime/runtime/src/lib.rs (L1554-1616)
```rust
            VersionedReceiptEnum::PromiseResume(data_receipt) => {
                if data_receipt.data.is_none() {
                    // This is a timeout resume. Check the status to see if the receipt has been resumed.
                    let status =
                        get_promise_yield_status(state_update, account_id, data_receipt.data_id)?;
                    if status == Some(PromiseYieldStatus::ResumeInitiated) {
                        // A non-timeout resume receipt has been sent, cancel the timeout.
                        return Ok(None);
                    }
                }

                // Received a new PromiseResume receipt delivering input data for a PromiseYield.
                // It is guaranteed that the PromiseYield has exactly one input data dependency
                // and that it arrives first, so we can simply find and execute it.
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

**File:** runtime/runtime/src/tests/apply.rs (L4978-5040)
```rust
// A FunctionCall whose receiver is deleted and recreated within the same chunk must
// resolve to the freshly recreated (no-code) account, not to a stale contract that
// `ReceiptPreparationPipeline` compiled against the receiver's code as resolved at
// preparation time.
#[test]
fn test_function_call_after_same_chunk_delete_recreate_resolves_fresh_code() {
    let parent = alice_account();
    let child: AccountId = "child.alice.near".parse().unwrap();
    // initial_locked must be 0 so the self-DeleteAccount receipt below passes the
    // DeleteAccountStaking check in `check_actor_permissions`.
    let (runtime, tries, root, mut apply_state, signers, epoch_info_provider) = setup_runtime(
        vec![parent.clone(), child.clone()],
        Balance::from_near(1_000_000),
        Balance::ZERO,
        Gas::from_teragas(1000),
    );
    let parent_signer = signers[0].clone();
    let child_signer = signers[1].clone();

    let deploy = create_receipt_with_actions(
        child.clone(),
        child_signer.clone(),
        vec![Action::DeployContract(DeployContractAction {
            code: near_test_contracts::trivial_contract().to_vec(),
        })],
    );
    let deploy_result = runtime
        .apply(
            tries.get_trie_for_shard(ShardUId::single_shard(), root),
            &None,
            &apply_state,
            &[deploy],
            SignedValidPeriodTransactions::empty(),
            &epoch_info_provider,
            Default::default(),
        )
        .unwrap();
    let root =
        commit_apply_result(&deploy_result, &mut apply_state, &tries, ShardUId::single_shard());
    apply_state.block_height += 1;

    let receipt_gas_price = GAS_PRICE.max(apply_state.config.min_gas_purchase_price);
    let build_receipt = |tag: &str, predecessor: AccountId, signer: &Signer, actions| -> Receipt {
        Receipt::V0(ReceiptV0 {
            predecessor_id: predecessor.clone(),
            receiver_id: child.clone(),
            receipt_id: CryptoHash::hash_borsh((tag, &child)),
            receipt: ReceiptEnum::Action(ActionReceipt {
                signer_id: predecessor,
                signer_public_key: signer.public_key(),
                gas_price: receipt_gas_price,
                output_data_receivers: vec![],
                input_data_ids: vec![],
                actions,
            }),
        })
    };
    let delete = build_receipt(
        "delete",
        child.clone(),
        &child_signer,
        vec![Action::DeleteAccount(DeleteAccountAction { beneficiary_id: parent.clone() })],
    );
```
