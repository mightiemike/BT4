### Title
Access-key nonce is not preserved on key re-creation, enabling replay of stale pre-signed transactions after key rotation - (File: `runtime/runtime/src/access_keys.rs`)

### Summary
When a `FullAccess` key deletes and then re-adds an access key that reuses the same public key, `add_regular_key` resets the key's nonce to a block-height-derived floor instead of preserving the nonce the deleted key had reached. This contradicts the documented security invariant and allows previously signed-but-unsubmitted transactions (e.g. from a leaked/hijacked signing key that the account owner tried to revoke by deleting and recreating the key) to be replayed after "rotation," analogous to a stale authentication credential remaining valid after logout/reset.

### Finding Description
The `AccessKey` struct documents an explicit anti-replay requirement: [1](#0-0) 

> "In some cases the access key needs to be recreated. If the new access key reuses the same public key, the nonce of the new access key should be equal to the nonce of the old access key. It's required to avoid replaying old transactions again." This same requirement is documented in `docs/DataStructures/AccessKey.md`: [2](#0-1) 

However, `add_regular_key` — invoked by `action_add_key` whenever a `AddKeyAction` targets a public key that is *not currently present* (i.e., was deleted, or never existed) — unconditionally sets the new access key's nonce to `initial_nonce_value(block_height)`, discarding any history of a previously-deleted key that shared the same public key: [3](#0-2) 

`initial_nonce_value` is purely a function of the current block height: [4](#0-3) 

Since `action_delete_key` fully removes the access key entry from the trie (`remove_access_key`) with no memory of its last nonce: [5](#0-4) 

...the floor assigned on recreation has no relationship to how far the previous incarnation's nonce had advanced. `verify_nonce` only requires `tx_nonce > current_nonce` (or `== current_nonce + 1` under `Strict` mode): [6](#0-5) 

So any previously valid, signed-but-not-yet-submitted transaction whose nonce falls in the window `(initial_nonce_value(new_block_height), old_max_nonce]` becomes admissible again once the key is deleted and re-added with the same public key, because the on-chain nonce baseline has been rolled back rather than preserved.

### Impact Explanation
An account owner who suspects a signing key has been compromised ("hijacked") and attempts a defensive delete-and-recreate of the same public key does not actually invalidate previously signed transactions issued under that key, provided the transaction's nonce still lies above the fresh floor. This can let an attacker replay a pre-signed `Transfer`, `FunctionCall`, or other action, causing unauthorized value movement or unintended contract calls after the victim believed the key had been rotated/revoked. Because `initial_nonce_value` only grows with block height while the old nonce grows with transaction volume, high-throughput accounts (e.g. relayers, bots, gas-key-heavy accounts) are the most exposed, since their nonce can outpace the block-height-derived floor within realistic timeframes.

### Likelihood Explanation
Reachable entirely through ordinary, unprivileged transactions: any holder of a `FullAccess` key on the account can submit a `DeleteKey` action followed later by an `AddKey` action reusing the same public key — both standard actions available to any transaction signer, with no special privilege required. Exploitation additionally requires the attacker to already hold a validly-signed but unsubmitted transaction (e.g., obtained via the same key compromise event that motivated the "rotation" in the first place), which is a realistic threat model directly mirroring the Laravel "hijacked remember-me cookie" scenario.

### Recommendation
When `action_add_key` re-adds a public key, check whether an access key with that public key existed previously (e.g., via a tombstone/last-nonce record, or by refusing to lower the nonce floor below any nonce ever observed for that public key on the account) and set the new key's nonce to at least the old key's last nonce, per the documented invariant, rather than always resetting to `initial_nonce_value(block_height)`.

### Proof of Concept
1. Account `alice.near` has `FullAccess` key `K` with current nonce `N` reached through normal usage within block height `H1`.
2. Attacker (who has compromised `K`) pre-signs a malicious `Transfer` transaction with nonce `N+5` but does not submit it.
3. Alice detects the compromise and, believing this revokes `K`, submits `DeleteKey(K)` followed by `AddKey(K, new_permissions)` at a later block height `H2` such that `initial_nonce_value(H2) < N+5`.
4. `add_regular_key` sets the recreated key's nonce to `initial_nonce_value(H2)`, which is below `N+5`.
5. The attacker submits the pre-signed transaction with nonce `N+5`; `verify_nonce` accepts it because `N+5 > initial_nonce_value(H2)`, and the transfer executes despite Alice's attempted key rotation.

### Citations

**File:** core/primitives-core/src/account.rs (L801-805)
```rust
pub struct AccessKey {
    /// Nonce for this access key, used for tx nonce generation. When access key is created, nonce
    /// is set to `(block_height - 1) * 1e6` to avoid tx hash collision on access key re-creation.
    /// See <https://github.com/near/nearcore/issues/3779> for more details.
    pub nonce: Nonce,
```

**File:** docs/DataStructures/AccessKey.md (L6-12)
```markdown
```rust
pub struct AccessKey {
    /// The nonce for this access key.
    /// NOTE: In some cases the access key needs to be recreated. If the new access key reuses the
    /// same public key, the nonce of the new access key should be equal to the nonce of the old
    /// access key. It's required to avoid replaying old transactions again.
    pub nonce: Nonce,
```

**File:** runtime/runtime/src/access_keys.rs (L46-50)
```rust
pub(crate) fn initial_nonce_value(block_height: BlockHeight) -> Nonce {
    // Set default nonce for newly created access key to avoid transaction hash collision.
    // See <https://github.com/near/nearcore/issues/3779>.
    (block_height - 1) * near_primitives::account::AccessKey::ACCESS_KEY_NONCE_RANGE_MULTIPLIER
}
```

**File:** runtime/runtime/src/access_keys.rs (L136-147)
```rust
fn delete_regular_key(
    fee_config: &RuntimeFeesConfig,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    public_key: &PublicKey,
    access_key: &AccessKey,
) {
    let storage_usage = access_key_storage_usage(fee_config, public_key, access_key);
    remove_access_key(state_update, account_id.clone(), public_key.clone());
    account.set_storage_usage(account.storage_usage().saturating_sub(storage_usage));
}
```

**File:** runtime/runtime/src/access_keys.rs (L230-255)
```rust
fn add_regular_key(
    fee_config: &RuntimeFeesConfig,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    public_key: &PublicKey,
    access_key: &AccessKey,
    block_height: BlockHeight,
) -> Result<(), StorageError> {
    let mut access_key = access_key.clone();
    access_key.nonce = initial_nonce_value(block_height);
    set_access_key(state_update, account_id.clone(), public_key.clone(), &access_key);

    account.set_storage_usage(
        account
            .storage_usage()
            .checked_add(access_key_storage_usage(fee_config, public_key, &access_key))
            .ok_or_else(|| {
                StorageError::StorageInconsistentState(format!(
                    "Storage usage integer overflow for account {}",
                    account_id
                ))
            })?,
    );
    Ok(())
}
```

**File:** runtime/runtime/src/verifier.rs (L253-271)
```rust
/// Verify that the transaction nonce is valid.
fn verify_nonce(
    tx_nonce: Nonce,
    current_nonce: Nonce,
    block_height: Option<BlockHeight>,
    nonce_mode: NonceMode,
) -> Result<(), InvalidTxError> {
    match nonce_mode {
        NonceMode::Monotonic => {
            if tx_nonce <= current_nonce {
                return Err(InvalidTxError::InvalidNonce { tx_nonce, ak_nonce: current_nonce });
            }
        }
        NonceMode::Strict => {
            if !current_nonce.checked_add(1).is_some_and(|expected| tx_nonce == expected) {
                return Err(InvalidTxError::InvalidNonce { tx_nonce, ak_nonce: current_nonce });
            }
        }
    }
```
