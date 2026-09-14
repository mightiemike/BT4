### Title
Unbounded per-receipt access-key/gas-key iteration in `remove_account` can stall chunk application - (File: `core/store/src/utils/mod.rs`)

### Summary
The reported bug is a classic "unbounded loop over a permissionlessly-grown, unremovable list" pattern: `TwapOracle.registerPair` lets anyone add pairs with no cap and no removal method, while `TwapOracle.update` must iterate every pair in one call, eventually making the oracle un-updatable. The closest reachable analog in nearcore is `remove_account` (`core/store/src/utils/mod.rs:504-575`), invoked by the unprivileged `DeleteAccount` action (`runtime/runtime/src/actions.rs`, `action_delete_account`). An account owner can accumulate an unbounded number of access keys and gas-key nonce entries via ordinary `AddKey` transactions over time (there is no on-chain limit on the number of access keys per account — the only limit, `access_keys_limit`, is enforced solely in the RPC/view layer, `runtime/runtime/src/state_viewer/mod.rs:185-277`, and does not gate `AddKeyAction`). A single `DeleteAccount` action must then iterate and remove *every* access key and every gas-key nonce entry for that account in one unbroken loop with no gas/compute check inside the loop.

### Finding Description
`remove_account` walks the whole `access_keys` trie-key range for an account and collects every key (and every gas-key nonce sub-entry) into `keys_to_remove` before removing them: [1](#0-0) 

There is no bound on the number of iterations and no check against the chunk's gas/compute budget while the loop runs — the cost (`gas_key_nonce_count`, `gas_key_nonce_total_key_bytes`) is only tallied and converted into a compute charge *after* the entire removal has already executed, as shown by the test that asserts `action_result.compute_usage` post-hoc: [2](#0-1) 

Crucially, the runtime's chunk-level compute/gas budget is only checked *between* receipts, not while a single receipt (here, one `DeleteAccount` action receipt) is executing: [3](#0-2) 

This is the same shape as the TwapOracle bug: nothing prevents an account from accumulating an arbitrarily large number of access keys via many independent, cheap `AddKeyAction` transactions (each individually gas-metered and paid-for at add time, and each within `max_number_bytes_method_names`/normal per-tx limits, so no single transaction is rejected), and nothing bounds or checks gas *during* the subsequent single-receipt removal loop. Access keys have no protocol-level maximum count and no forced cleanup path other than one-by-one `DeleteKeyAction`s or this unbounded `remove_account` sweep.

### Impact Explanation
A single `DeleteAccount` receipt against an account holding a very large number of access keys (and/or gas-key nonce rows, which multiply the entries per key up to `MAX_NONCES_FOR_GAS_KEY = 1024` each) forces the chunk-application code to perform an unbounded trie scan and an unbounded number of trie removals within one receipt, with the actual compute/gas cost determined only after the work is done. Because the chunk's gas/compute-limit check (`process_receipts`) is evaluated only *before starting the next receipt*, not while a receipt is running, this single receipt can consume execution time and I/O far beyond the intended per-chunk budget in one shot. This can:
- Stall or drastically slow chunk production/validation for the shard hosting the account (a transaction-triggered halt/DoS on block progress), since chunk producers and chunk validators must all execute the same unbounded loop to reproduce the state transition deterministically.
- Cause chunk-witness / storage-proof generation for that receipt to grow far past the intended per-chunk limits (each removed trie entry adds recorded storage proof), risking state-witness bloat beyond the documented 21 MiB budget described in `docs/misc/state_witness_size_limits.md`, which is exactly the kind of overrun the protocol's storage-proof limits were designed to prevent elsewhere (e.g. `EnforcePerReceiptStorageProofLimit`), but that limit is checked in the receipt-processing driver loops, not inside `remove_account`'s internal key-collection loop.
- Because the same account can be re-populated with new access keys before being deleted again, this is a repeatable, self-funded (only storage-staking cost, which is refunded on deletion) griefing vector rather than a one-time cost.

This matches the TwapOracle failure mode: a permissionless, unbounded "registration" (`AddKeyAction`) feeding an "update"-style operation (`DeleteAccount` → `remove_account`) that must process the entire unbounded set in one atomic step with no partial-progress or gas-checked short-circuit.

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to first build up a large number of access keys under their own account (paying storage cost per key, which is refunded upon deletion, so the attack is nearly self-funding over time) and then issue one `DeleteAccount` action. Both `AddKeyAction` and `DeleteAccountAction` are ordinary, unprivileged actions available to any signer with an access key on their own account — no validator, operator, or protocol-privileged role is needed. The main friction is the time/gas needed to accumulate enough keys, and the storage cost paid until refunded at deletion, but this is bounded attacker cost, not attacker-prohibitive.

### Recommendation
- Enforce a protocol-level maximum number of access keys (and/or gas-key nonce entries) per account, checked at `AddKeyAction`/`add_regular_key`/`add_gas_key` time (analogous to `MAX_NONCES_FOR_GAS_KEY`), so `remove_account`'s iteration is bounded by a known constant.
- Alternatively (or additionally), make `remove_account`'s key-collection loop gas/compute-aware, breaking out and deferring the remainder of the removal (e.g., via a continuation/delayed receipt) once a per-receipt compute budget is exceeded, mirroring the pattern already used for delayed/incoming receipt processing (`process_delayed_receipts`, `process_incoming_receipts` in `runtime/runtime/src/lib.rs`) and yield-timeout processing (`resolve_promise_yield_timeouts`), all of which already check `compute_limit`/`check_proof_size_limit_exceed` inside their loops.
- Charge/estimate the removal cost proportional to the *actual* number of keys before performing the trie walk, and reject/defer the `DeleteAccount` action if the attached gas cannot cover the known key count (which can be tracked incrementally in account storage_usage-derived metadata) rather than discovering the cost only after the unbounded work has executed.

### Proof of Concept
1. From an unprivileged account, submit many independent `AddKeyAction` transactions (or gas-key `AddKeyAction`s with `num_nonces` near `MAX_NONCES_FOR_GAS_KEY = 1024` each) over successive blocks to accumulate a very large number of access-key/gas-key-nonce trie entries under one account — each transaction is valid and individually cheap, and storage cost is refunded on deletion.
2. Submit a single `DeleteAccountAction` receipt for that account.
3. Observe that `action_delete_account` → `remove_account` (`core/store/src/utils/mod.rs:504-575`) must scan and remove every one of the accumulated entries within this one receipt, with the chunk-level compute/gas limit check (`runtime/runtime/src/lib.rs:2591-2606`) never firing mid-receipt — only being evaluated again before the *next* receipt starts — so the single `DeleteAccount` receipt's execution time/I-O and recorded storage-proof size scale directly and without an internal cutoff with however many keys the attacker accumulated.

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

**File:** runtime/runtime/src/access_keys.rs (L678-713)
```rust
    #[test]
    fn test_delete_account_removes_gas_keys() {
        let (account_id, public_key, access_key) = test_account_keys();
        let public_keys: Vec<PublicKey> = (0..3)
            .map(|i| PublicKey::from_seed(KeyType::ED25519, &format!("gas_key_{i}")))
            .collect();
        let mut state_update = setup_account(&account_id, &public_key, &access_key);
        let mut account = get_account(&state_update, &account_id).unwrap().unwrap();
        for public_key in &public_keys {
            add_gas_key_to_account(&mut state_update, &mut account, &account_id, public_key);
        }
        state_update.commit(StateChangeCause::InitialState);

        let action_result = test_delete_account(
            &account_id,
            AccountContract::from_local_code_hash(CryptoHash::default()),
            100,
            PROTOCOL_VERSION,
            &mut state_update,
        );
        assert!(action_result.result.is_ok());
        state_update.commit(StateChangeCause::InitialState);

        let expected_compute: u64 = public_keys
            .iter()
            .map(|pk| expected_nonce_remove_compute(&account_id, pk, TEST_NUM_NONCES as usize))
            .sum();
        assert_eq!(action_result.compute_usage, expected_compute);

        let lock = state_update.trie().lock_for_iter();
        let trie_key_count = state_update
            .locked_iter(&trie_key_parsers::get_raw_prefix_for_access_keys(&account_id), &lock)
            .expect("could not get trie iterator")
            .count();
        assert_eq!(trie_key_count, 0);
    }
```

**File:** runtime/runtime/src/lib.rs (L2591-2606)
```rust
        loop {
            if processing_state.total.compute >= compute_limit
                || processing_state.state_update.trie.check_proof_size_limit_exceed()
            {
                break;
            }

            let receipt = if let Some(receipt) = processing_state
                .delayed_receipts
                .pop(&mut processing_state.state_update, &processing_state.apply_state.config)?
            {
                receipt.into_receipt()
            } else {
                // Break loop if there are no more receipts to be processed.
                break;
            };
```
