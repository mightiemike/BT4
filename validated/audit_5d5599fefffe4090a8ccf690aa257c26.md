## Analysis Result

### Title
DeleteAccount does not clear PromiseYield state, allowing an orphaned yield receipt to execute against an account created later at the same account ID - (File: `runtime/runtime/src/actions.rs`)

### Summary
This nearcore code exhibits the same bug class as the reported Safe `LivenessGuard`/`LivenessModule` issue: a "normal" code path maintains an invariant by deleting an entry from a per-account mapping when the entity is removed, but a second, bypassing path removes the entity without performing that cleanup, leaving the mapping's stale entry reachable and later actionable.

### Finding Description
When a `PromiseYield` receipt is created, the runtime stores the pending callback receipt keyed by `(receiver_id, data_id)` in `PromiseYieldReceipt`, plus a `PromiseYieldStatus` entry and (for `yield_create_with_id`) `YieldIdToDataId`/`DataIdToYieldId` mappings. [1](#0-0) [2](#0-1) 

The "normal" cleanup path is `PromiseResume` handling in `Runtime::process_receipt`, which explicitly removes `PromiseYieldReceipt`, `PromiseYieldStatus`, and the yield-id mappings before executing the parked receipt: [3](#0-2) 

However, `DeleteAccountAction` is handled by `action_delete_account`, which calls `remove_account` and then unconditionally clears the account: [4](#0-3) 

`remove_account` only removes `Account`, `ContractCode`, access keys, gas-key nonces, and `ContractData` — it never touches `PromiseYieldReceipt`, `PromiseYieldStatus`, `YieldIdToDataId`/`DataIdToYieldId`, or the pending `PromiseYieldTimeout` queue entry for that account: [5](#0-4) 

This is functionally identical to the reported bug: `LivenessModule.removeOwners()` bypasses `LivenessGuard`'s cleanup logic that a normal Safe transaction would trigger. Here, `DeleteAccountAction` bypasses the cleanup logic that a normal `PromiseResume` would trigger, leaving a `PromiseYieldReceipt` (which embeds the *entire pending action receipt*, including its `actions` list) and its corresponding `PromiseYieldTimeout` queue entry alive in the trie after the owning account no longer exists.

`resolve_promise_yield_timeouts` walks the `PromiseYieldTimeout` queue independent of whether the account still exists — it only checks whether the `PromiseYieldReceipt` key is present in state: [6](#0-5) 

Because account IDs (including sub-accounts and, in principle, implicit accounts) can be re-created after deletion, a new owner of the same `account_id` can later be targeted by this stale, still-queued timeout: the runtime synthesizes a `PromiseResume` receipt for that `account_id`/`data_id`, finds the orphaned `PromiseYieldReceipt` still in state, and executes it via `apply_action_receipt` as if it were validly initiated by the (new) account — running the original owner's leftover actions against the storage of the new account occupant, entirely without the new owner's consent. [7](#0-6) 

Nothing in `action_delete_account`'s storage-usage/deletion-size checks accounts for this leftover state either, since `create_promise_yield_receipt`/`set_promise_yield_status` never update `account.storage_usage()`: [8](#0-7) 
so `DeleteAccountAction` succeeds unconditionally regardless of pending yields, and — if the account is never re-created — the orphaned trie entries persist forever, permanently bloating state (violating the invariant that a deleted account leaves no reachable state, analogous to the removed-owner-must-not-be-in-`lastLive` invariant in the report).

### Impact Explanation
- If the account ID is never re-created, the orphaned `PromiseYieldReceipt`, `PromiseYieldStatus`, `YieldIdToDataId`/`DataIdToYieldId`, and the still-queued `PromiseYieldTimeout` entry remain in the trie indefinitely — permanent, unaccounted-for state bloat that no garbage collection path removes (medium impact, storage invariant violation, matches the "permanently frozen"/unbounded state class).
- If the account ID is re-created (a legitimate NEAR pattern for sub-accounts and implicit accounts), the still-pending timeout will eventually fire and cause the runtime to execute the *original* owner's leftover action receipt against the *new* account's state, without any authorization from the new account controller — an invalid state transition / unauthorized execution triggered purely by a single account-deletion transaction plus the passage of time.

### Likelihood Explanation
High. Any unprivileged account can trigger this deterministically: call `yield_create` (or `yield_create_with_id`) then submit `DeleteAccountAction` before the yield is resumed or times out. No special privileges, races, or validator cooperation are required — a single transaction sequence from a normal signer suffices.

### Recommendation
`remove_account` (or `action_delete_account`) should also enumerate and delete all `PromiseYieldReceipt`, `PromiseYieldStatus`, `YieldIdToDataId`, and `DataIdToYieldId` entries for the account being deleted, mirroring the cleanup already performed on the `PromiseResume` path in `Runtime::process_receipt` (`lib.rs:1450-1470`). Additionally, `resolve_promise_yield_timeouts` should verify the target account still exists before synthesizing/executing a `PromiseResume`, and/or the queued `PromiseYieldTimeout` entry for a deleted account should be proactively purged (or the receipt marked dead) at deletion time, analogous to the proposed `removeLiveness()` cleanup hook in the original report.

### Proof of Concept
1. Account `alice.foo.near` deploys a contract and calls `yield_create` (or `yield_create_with_id`), which stores a `PromiseYieldReceipt`/`PromiseYieldStatus` and enqueues a `PromiseYieldTimeout` entry, per `create_promise_yield_receipt`/`enqueue_promise_yield_timeout` (`runtime/runtime/src/ext.rs:353-368`, `runtime/runtime/src/function_call.rs:154-172`).
2. Before the yield resumes or times out, `alice.foo.near` submits `DeleteAccountAction`, which runs through `action_delete_account` → `remove_account` (`runtime/runtime/src/actions.rs:299-374`, `core/store/src/utils/mod.rs:486-556`); this deletes the `Account`/keys/contract data but leaves the `PromiseYieldReceipt`, `PromiseYieldStatus`, and `PromiseYieldTimeout` queue entry intact in the trie.
3. A third party later creates a new account also named `alice.foo.near` (permitted once the old account no longer exists).
4. When the still-queued timeout's `expires_at` height passes, `resolve_promise_yield_timeouts` finds the stale `PromiseYieldReceipt` still present in state for `alice.foo.near`/`data_id`, and forwards a `PromiseResume` receipt for it (`runtime/runtime/src/lib.rs:2979-3031`).
5. `Runtime::process_receipt` handles the `PromiseResume`, finds the orphaned yield receipt, and executes it via `apply_action_receipt` against the *new* `alice.foo.near` account's state (`runtime/runtime/src/lib.rs:1444-1495`) — executing the original owner's leftover actions without the new owner's authorization.

### Citations

**File:** core/store/src/utils/mod.rs (L182-194)
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

**File:** runtime/runtime/src/ext.rs (L353-369)
```rust
    fn create_promise_yield_receipt(
        &mut self,
        receiver_id: AccountId,
    ) -> Result<(ReceiptIndex, CryptoHash), VMLogicError> {
        let input_data_id = self.generate_data_id();
        let receipt_index =
            self.receipt_manager.create_promise_yield_receipt(input_data_id, receiver_id.clone());

        set_promise_yield_status(
            &mut self.trie_update,
            &receiver_id,
            input_data_id,
            PromiseYieldStatus::Yielded,
        );

        Ok((receipt_index, input_data_id))
    }
```

**File:** runtime/runtime/src/lib.rs (L1444-1495)
```rust
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

**File:** runtime/runtime/src/lib.rs (L2979-3031)
```rust
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

            // Record a ReceiptToTx entry for the new resume receipt. The parent is the
            // yield receipt that is being timed out.
            if processing_state.apply_state.save_receipt_to_tx {
                let yield_receipt: Receipt = get_pure(state_update, &promise_yield_key)?
                    .expect("promise yield receipt should exist since contains_key was true");
                processing_state.receipt_to_tx.push((
                    new_receipt_id,
                    ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
                        origin: ReceiptOrigin::FromReceipt(ReceiptOriginReceipt {
                            parent_receipt_id: *yield_receipt.receipt_id(),
                            parent_predecessor_id: yield_receipt.predecessor_id().clone(),
                        }),
                        receiver_account_id: queue_entry.account_id.clone(),
                        shard_id: processing_state.apply_state.shard_id,
                    }),
                ));
            }

            // The receipt is destined for the local shard and will be placed in the outgoing
            // receipts buffer. It is possible that there is already an outgoing receipt resolving
            // this yield if `yield_resume` was invoked by some receipt which was processed in
            // the current chunk. The ordering will be maintained because the receipts are
            // destined for the same shard; the timeout will be processed second and discarded.
            receipt_sink.forward_or_buffer_receipt(
                resume_receipt,
                apply_state,
                &mut state_update,
            )?;
        }
```

**File:** runtime/runtime/src/actions.rs (L349-374)
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
    Ok(())
```
