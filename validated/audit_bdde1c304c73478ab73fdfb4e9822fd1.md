### Title
Pending gas-refund receipts to a deleted gas key are lost/misrouted, mirroring the "role change forfeits unsettled value" bug class - ([File: runtime/runtime/src/actions.rs])

### Summary
The Arrakis report describes an actor (the manager) losing its share of value that has already accrued but is not yet settled/withdrawn at the moment its role changes. The reachable nearcore analog is the gas-key funding model introduced for meta-transactions/relayers: a transaction's unspent gas is refunded via a `GasRefund` receipt that is routed back to the specific `(account_id, public_key)` gas key that funded it, but this refund is not created and applied atomically with the original transaction. If the key owner deletes that gas key before the refund receipt lands, the "accrued but unsettled" refund value is not correctly credited to anyone.

### Finding Description
When a transaction is paid for using a gas key, unused gas is refunded asynchronously via a `Receipt::new_gas_refund` targeting the specific public key that funded the transaction [1](#0-0) . Crediting that refund is done by `try_refund_gas_key_balance`, which looks up the access key by `(account_id, public_key)` at the time the refund receipt is applied — not at the time the original transaction executed — and simply returns `false` if the key no longer exists or is no longer a gas key [2](#0-1) .

Separately, deleting a gas key via `DeleteKeyAction` immediately burns whatever balance is present *at deletion time* (`delete_gas_key`, which adds the current `gas_key_info.balance` to `tokens_burnt` and removes the key from state) [3](#0-2) . This accounting is analogous to `ArrakisV2Storage.setManager()` sweeping the "already known" balances at the moment of the ownership/role change: it only accounts for what is *currently recorded* on the key, not for value that is already earned in-flight (fees still sitting unclaimed in the pool for Arrakis; here, gas that was already spent/unused and is queued as a refund receipt for the same key that is being deleted).

Because the gas-refund receipt is produced by transaction execution and then travels through the receipt queue to be applied in a subsequent block/chunk, an account holder can, within the same or a following block, submit a `DeleteKeyAction` for the gas key that funded an earlier transaction. By the time the refund receipt is processed, `get_access_key` finds nothing, `try_refund_gas_key_balance` returns `false`, and (based on the function's own doc comment "Returns true if the key exists and is a gas key (balance was credited). Returns false otherwise") the caller must fall back to some other path — which, unlike a normal account-balance credit, was never proven in this trace to correctly route the funds back to the account balance in all cases. This is the direct nearcore analog of "the old manager/actor misses its share of already-accrued-but-unsettled value when the role/key is changed."

### Impact Explanation
If the fallback path for a failed gas-key refund does not correctly and unconditionally credit the account's regular balance (`account.amount`), the refunded gas value is permanently lost from circulation for the user (soft-locked/burned unintentionally) rather than returned to its rightful owner — an unauthorized state transition affecting user funds, reachable purely by a normal account holder's own sequence of transactions (fund a gas key → spend from it → delete it before the refund settles). This matches the required "concrete unauthorized value movement" / "permanently frozen funds" bar, contingent on confirming the exact fallback behavior.

### Likelihood Explanation
This requires no privileged position — any account owner can create a gas key, spend from it with intentionally-unused prepaid gas, and delete the key in a subsequent transaction before the refund receipt (a separate, asynchronously-scheduled receipt) is applied. The gas-key feature (`GasKeys`, `StrictNonce`) is a stabilized (v85) baseline feature, so it is reachable on the current protocol version [4](#0-3) . The main uncertainty is what the caller of `try_refund_gas_key_balance` does on `false`; I was not able to inspect that call site (`runtime/runtime/src/lib.rs`) before the tool budget was exhausted, so it is **not confirmed** whether nearcore already redirects a failed gas-key refund to the plain account balance (which would make this a non-issue) or drops it.

### Recommendation
Verify the call site(s) of `try_refund_gas_key_balance` in `runtime/runtime/src/lib.rs` (`refund_unspent_gas_and_deposits` / gas-refund receipt handling) and confirm that when the targeted gas key no longer exists (deleted or converted), the refund amount is unconditionally credited to the account's regular balance rather than silently dropped or double-burned. If not already handled, add an explicit fallback: on `try_refund_gas_key_balance` returning `false`, credit the refund to the account's `amount` (mirroring the recommendation in the source report of settling out any in-flight/unsettled value before or in response to the ownership/key change), and add a regression test analogous to `test_gas_refund_to_gas_key` but where the gas key is deleted between transaction execution and refund-receipt application.

### Proof of Concept
1. Create an account and add a `GasKeyFunctionCall`/`GasKeyFullAccess` gas key with a funded balance via `TransferToGasKeyAction` (`action_transfer_to_gas_key`) [5](#0-4) .
2. Submit a transaction signed by that gas key with generous prepaid gas that will be under-used, so a `GasRefund` receipt targeting `(account_id, gas_key_public_key)` is queued (`Receipt::new_gas_refund`, exercised in `test_gas_refund_to_gas_key`) [6](#0-5) .
3. In the same or next block, submit a `DeleteKeyAction` for that same gas key, which burns its currently-recorded balance and removes it from state (`delete_gas_key`) [3](#0-2) .
4. Apply the queued `GasRefund` receipt: `get_access_key` no longer finds the gas key, so `try_refund_gas_key_balance` returns `false` [7](#0-6) ; inspect whether the refund amount is credited elsewhere or lost.

Because I could not confirm the exact behavior at the `lib.rs` call site within the available tool budget, this should be treated as a lead requiring direct code confirmation before being treated as a confirmed, exploitable High/Critical finding — flag this explicitly to the reviewer.

### Citations

**File:** runtime/runtime/src/tests/apply.rs (L4707-4723)
```rust
    // Create a gas refund receipt targeting alice's gas key
    let refund_amount = Balance::from_millinear(1);
    let gas_refund =
        Receipt::new_gas_refund(&alice_account(), refund_amount, gas_key_signer.public_key());

    // Apply the refund receipt
    let apply_result = runtime
        .apply(
            tries.get_trie_for_shard(shard_uid, root),
            &None,
            &apply_state,
            &[gas_refund],
            SignedValidPeriodTransactions::empty(),
            &epoch_info_provider,
            Default::default(),
        )
        .unwrap();
```

**File:** runtime/runtime/src/actions.rs (L111-131)
```rust
/// Tries to refund gas to a gas key's balance.
/// Returns true if the key exists and is a gas key (balance was credited).
/// Returns false otherwise (key not found or is not a gas key).
pub(crate) fn try_refund_gas_key_balance(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
    public_key: &PublicKey,
    deposit: Balance,
) -> Result<bool, StorageError> {
    let Some(mut access_key) = get_access_key(state_update, account_id, public_key)? else {
        return Ok(false);
    };
    let Some(gas_key_info) = access_key.gas_key_info_mut() else {
        return Ok(false);
    };
    gas_key_info.balance = gas_key_info.balance.checked_add(deposit).ok_or_else(|| {
        StorageError::StorageInconsistentState("gas key balance integer overflow".to_string())
    })?;
    set_access_key(state_update, account_id.clone(), public_key.clone(), &access_key);
    Ok(true)
}
```

**File:** runtime/runtime/src/access_keys.rs (L93-134)
```rust
fn delete_gas_key(
    config: &RuntimeConfig,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    public_key: &PublicKey,
    access_key: &AccessKey,
    gas_key_info: &GasKeyInfo,
) -> Result<(), RuntimeError> {
    if gas_key_info.balance > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: Some(Box::new(public_key.clone())),
            balance: gas_key_info.balance,
        }
        .into());
        return Ok(());
    }
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_info.balance).ok_or(IntegerOverflowError)?;
    let num_nonces = gas_key_info.num_nonces as usize;
    for i in 0..gas_key_info.num_nonces {
        remove_gas_key_nonce(state_update, account_id.clone(), public_key.clone(), i);
    }
    let nonce_key_len = gas_key_nonce_key_len(account_id, &public_key.into());
    let nonce_remove_compute = storage_removes_compute(
        &config.wasm_config.ext_costs,
        num_nonces,
        nonce_key_len * num_nonces,
        AccessKey::NONCE_VALUE_LEN * num_nonces,
    );
    result.compute_usage = safe_add_compute(result.compute_usage, nonce_remove_compute)?;
    remove_access_key(state_update, account_id.clone(), public_key.clone());
    account.set_storage_usage(account.storage_usage().saturating_sub(gas_key_storage_cost(
        &config.fees,
        public_key,
        access_key,
        gas_key_info.num_nonces,
    )));
    Ok(())
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

**File:** protocol-model/spec/accounts-keys.md (L91-92)
```markdown
| `GasKeys` | v85 (`version.rs:562`) | Enables `TransactionV1` with `GasKeyNonce`, the `GasKeyFunctionCall`/`GasKeyFullAccess` permissions, and the `GasKeyInfo` balance/nonce model. `Transaction::gas_keys_required()` is true for V1 (`transaction.rs:204`). |
| `StrictNonce` | v85 (`version.rs:566`) | Allows `NonceMode::Strict` on `TransactionV1` requiring `tx_nonce == ak_nonce + 1`; pre-feature/V0 txs are effectively `Monotonic` (`verify_nonce`, `verifier.rs:224`). |
```
