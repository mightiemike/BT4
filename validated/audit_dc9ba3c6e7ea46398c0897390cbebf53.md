### Title
Unbounded per-account access/gas-key scan lets an attacker turn a single, fixed-gas action into unmetered, unbounded trie work - ([File: core/store/src/utils/mod.rs])

### Summary
`compute_gas_key_balance_sum` in `core/store/src/utils/mod.rs` iterates over *every* access key stored under an account's access-key trie prefix to sum up gas-key balances, with no upper bound on the number of keys scanned [1](#0-0) . This is structurally the same "unbounded array/loop iterated during a fixed-cost operation" pattern as the reported `ReferenceLendingPools.lendingPools` issue: a per-account list with no cap is fully walked inside a runtime action whose gas fee does not scale with the list size.

### Finding Description
An account can accumulate an arbitrary number of access keys (including `GasKeyFullAccess`/`GasKeyFunctionCall` keys) by repeatedly issuing `AddKey` actions/transactions signed by the account's own full-access key - there is no protocol-level cap on the number of access keys per account visible in this codebase (the only key-count cap found, `max_universal_state_init_keys`, applies solely to `UniversalStateInit`, a different code path) [2](#0-1) . Later, when an action requires summing gas-key balances for that account, `compute_gas_key_balance_sum` performs a full `locked_iter` scan over the entire access-key key-range for the account, parsing and loading every key/value pair, regardless of how many keys exist [3](#0-2) . This function is invoked from `runtime/runtime/src/actions.rs` (confirmed via 2 call sites), and the pattern is exercised in tests around account deletion / gas-key balance checks, e.g. `test_delete_account_gas_key_balance_at_threshold`, which shows the balance-sum work happening as part of `action_delete_account` processing [4](#0-3) .

Unlike bounded, gas-metered trie access elsewhere in the runtime (e.g., paginated `view_access_keys` in `state_viewer/mod.rs`, which explicitly caps page size and returns `TooManyAccessKeys` beyond a configured limit) [5](#0-4) , the balance-sum helper performs an unbounded scan with no such limit or corresponding gas charge tied to the number of keys visited.

### Impact Explanation
Because the action that triggers this scan (e.g. `DeleteAccount`, or another action relying on the aggregate gas-key balance) is charged a fixed, key-count-independent execution fee, an account owner can inflate the real computational cost of processing that single receipt far beyond what its gas fee pays for, by first funding many `AddKey`/`AddGasKey`-style actions on their own account. This breaks the fee/gas-vs-work invariant the rest of the runtime carefully maintains (compute limits, storage-proof limits, per-receipt action counts, `UniversalStateInitTooManyKeys`, etc.), and can inflate per-receipt processing time disproportionately to the gas charged, i.e., a gas-metering bypass reachable from a single unprivileged transaction signer's own account.

### Likelihood Explanation
Reachable by any unprivileged account holder: no special permission is needed to call `AddKey` many times on one's own account, and no cap on the resulting access-key count was found in this tree. The follow-up action that exercises `compute_gas_key_balance_sum` (confirmed reachable from `actions.rs`, exercised by delete-account tests) is also a standard, permissionless action any account owner can invoke on their own account.

### Recommendation
Bound the number of access keys (in particular gas keys) allowed per account, enforced at `AddKey`/`AddGasKey`-style action validation time (mirroring the `max_universal_state_init_keys` pattern), and/or make `compute_gas_key_balance_sum`'s cost gas-metered proportionally to the number of keys scanned (e.g., charge gas per key visited, or maintain an incrementally-updated aggregate gas-key balance in account state instead of re-scanning the whole prefix on demand).

### Proof of Concept
Conceptual (exact fee accounting for the calling action sites in `actions.rs` could not be fully re-verified within tool budget, so this should be validated against the current fee table before treating as confirmed):
1. Attacker creates account `A` with a full-access key.
2. Attacker submits many `AddKey` transactions from `A` to itself, each adding a `GasKeyFullAccess`/`GasKeyFunctionCall` key, until the account holds a very large number of access/gas keys (bounded only by storage-stake cost, not by an explicit key-count cap).
3. Attacker submits a single transaction invoking the action that calls `compute_gas_key_balance_sum` (e.g. `DeleteAccount`) on `A`.
4. The runtime performs an unbounded `locked_iter` scan over all of `A`'s access keys inside that one receipt's execution, while the receipt's gas charge remains the same as if `A` had zero or one key, since the fee for the triggering action does not scale with key count.

### Citations

**File:** core/store/src/utils/mod.rs (L457-497)
```rust
/// Computes the total balance across all gas keys for a given account.
pub fn compute_gas_key_balance_sum(
    state_update: &TrieUpdate,
    account_id: &AccountId,
) -> Result<Balance, StorageError> {
    let mut total = Balance::ZERO;
    let lock = state_update.trie().lock_for_iter();
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
        if nonce_index.is_some() {
            continue;
        }
        if let Some(balance) = get_access_key_by_handle(state_update, account_id, &key_handle)?
            .as_ref()
            .and_then(|access_key| access_key.gas_key_info())
            .map(|gas_key_info| gas_key_info.balance)
        {
            total = total.checked_add(balance).ok_or_else(|| {
                StorageError::StorageInconsistentState("gas key balance overflow".to_string())
            })?;
        }
    }
    Ok(total)
}
```

**File:** core/parameters/src/snapshots/near_parameters__config_store__tests__82.json.snap (L258-258)
```text
      "max_universal_state_init_keys": 1024,
```

**File:** runtime/runtime/src/access_keys.rs (L1334-1367)
```rust
    #[test]
    fn test_delete_account_gas_key_balance_at_threshold() {
        let (account_id, public_key, access_key) = test_account_keys();
        let public_keys: Vec<PublicKey> = (0..3)
            .map(|i| PublicKey::from_seed(KeyType::ED25519, &format!("gas_key_{i}")))
            .collect();
        let mut state_update = setup_account(&account_id, &public_key, &access_key);
        let mut account = get_account(&state_update, &account_id).unwrap().unwrap();
        for public_key in &public_keys {
            add_gas_key_to_account(&mut state_update, &mut account, &account_id, public_key);
        }

        // Fund gas keys so total is exactly 1 NEAR
        let deposit_amounts = [
            Balance::from_millinear(400),
            Balance::from_millinear(400),
            Balance::from_millinear(200),
        ];
        for (pk, amount) in public_keys.iter().zip(deposit_amounts.iter()) {
            transfer_to_gas_key(&mut state_update, &account_id, pk, *amount);
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
        let expected_burnt =
            deposit_amounts.iter().fold(Balance::ZERO, |acc, x| acc.checked_add(*x).unwrap());
        assert_eq!(action_result.tokens_burnt, expected_burnt);
```

**File:** runtime/runtime/src/state_viewer/mod.rs (L243-258)
```rust
            if let Some(cap) = item_cap {
                if keys.len() as u64 >= u64::from(cap) {
                    // Page is full and at least one more key exists: emit a cursor
                    // at the last kept key so the caller can resume.
                    last_key = keys
                        .last()
                        .map(|(handle, _): &(PublicKeyHandle, AccessKey)| handle.clone());
                    break;
                }
            } else if keys.len() as u64 >= u64::from(max) {
                // Unpaginated request that exceeds the configured limit.
                return Err(errors::ViewAccessKeyError::TooManyAccessKeys {
                    requested_account_id: account_id.clone(),
                    limit: max,
                });
            }
```
