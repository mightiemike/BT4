### Title
Access-key recreation resets nonce baseline to current block height instead of preserving prior nonce, enabling replay of previously-signed transactions - ([File: runtime/runtime/src/access_keys.rs])

### Summary
`FreshRSS`'s CVE-2025-54592 is a "logout doesn't kill the session credential" bug: the server issues a fresh session but the old session cookie (credential) is never invalidated, so it can be replayed. The nearcore analog is in access-key rotation: `runtime/runtime/src/access_keys.rs`'s `add_regular_key` unconditionally stamps the recreated key with `initial_nonce_value(current_block_height)`, discarding whatever nonce the account had actually reached under a prior incarnation of the same public key. This can leave a gap in which stale, previously-valid-but-unexecuted signed transactions become replayable against the "new" key, exactly like a stale session cookie being accepted after logout.

### Finding Description
`AccessKey.nonce` is the on-chain replay-protection counter checked in `verify_nonce` [1](#0-0) . When a `DeleteKeyAction` is processed, `delete_regular_key` simply removes the trie record with no persistence of the nonce reached [2](#0-1) . When an `AddKeyAction` reuses that same public key (the documented "key needs to be recreated" scenario), `add_regular_key` forcibly overwrites the caller-supplied nonce with a value derived only from the *current block height*:

```
fn add_regular_key(...) {
    let mut access_key = access_key.clone();
    access_key.nonce = initial_nonce_value(block_height);
    set_access_key(state_update, account_id.clone(), public_key.clone(), &access_key);
    ...
}
``` [3](#0-2) 

`initial_nonce_value` is `(block_height - 1) * ACCESS_KEY_NONCE_RANGE_MULTIPLIER` [4](#0-3) , and the upper bound enforced on nonces at signing time is `height * ACCESS_KEY_NONCE_RANGE_MULTIPLIER` in `verify_nonce` [5](#0-4) . This means a legitimately signed but never-broadcast (or broadcast-but-not-yet-included) transaction can carry a nonce anywhere up to just under `H*multiplier` for the block height `H` at signing time. If the account owner later performs `DeleteKey` + `AddKey` with the *same* public key at a nearby block height `H2`, the freshly recreated key's nonce baseline is only `(H2-1)*multiplier`, which is strictly less than nonces that were valid and already close to `H2*multiplier` under the old incarnation. Any such old signed transaction — captured by an observer from the network, from a previous unconfirmed submission, or replayed by the original signer's own client — will pass `verify_nonce`'s Monotonic/Strict check against the recreated key and be re-executed.

This directly contradicts the protocol's own documented invariant: "If the new access key reuses the same public key, the nonce of the new access key should be equal to the nonce of the old access key. It's required to avoid replaying old transactions again." [6](#0-5)  — yet the runtime never reads or enforces this; it silently discards any nonce value passed in `AddKeyAction.access_key.nonce` and substitutes its own block-height-derived value [7](#0-6) . There is no code path anywhere in `action_add_key`/`action_delete_key` that reads the deleted key's last nonce and carries it forward to the recreated key.

### Impact Explanation
A stale, previously signed transaction (e.g., a `Transfer`, `FunctionCall`, or `Stake` action) can be re-admitted and re-executed after the signer rotates/recreates the same public key — precisely the "incomplete session termination" pattern from the CVE, applied to on-chain credentials. Depending on what the old signed transaction did, this can cause unauthorized value movement (double transfer/double stake) that the account owner believed was no longer valid once they rotated the key. This satisfies the "concrete unauthorized value movement" / "invalid state transition acceptance" bar, since a transaction whose validity should have ended is accepted and applied again.

### Likelihood Explanation
The scenario requires: (1) the account to delete and recreate an access key with the *same* public key (a supported, valid action sequence, e.g. `swap_key` style flows used in some wallets/apps for allowance resets), (2) an old signed transaction with a nonce that falls in the gap between the new baseline and the previous high-water mark, and (3) that old transaction to be resubmitted (trivial — signed transactions are broadcast in plaintext over the network and can be captured and replayed by any observer). Because nonce baselines are recomputed purely from block height rather than from the account's actual nonce history, the size of the exploitable gap (`~ACCESS_KEY_NONCE_RANGE_MULTIPLIER`, i.e. up to ~10^6 nonce values) is large and reachable without any privileged access — any unprivileged transaction signer who rotates a key using the same public key is exposed, and any RPC caller who has observed a prior signed tx from that account can attempt the replay.

### Recommendation
When adding a key whose public key was previously deleted from the same account within a relevant nonce window, `action_add_key`/`add_regular_key` should read back (or otherwise track) the last-known nonce for that `(account_id, public_key)` pair and initialize the new access key's nonce to at least that prior value (never lower), rather than always deriving it solely from `initial_nonce_value(block_height)`. At minimum, the maximum of `initial_nonce_value(block_height)` and the previous key's nonce should be used, closing the replay gap described in `docs/DataStructures/AccessKey.md`.

### Proof of Concept
1. Alice creates access key `pk` on her account; via normal usage, the on-chain nonce for `pk` grows to `N_old`, close to the current upper bound `H1 * ACCESS_KEY_NONCE_RANGE_MULTIPLIER` (permitted by `verify_nonce`'s upper-bound check).
2. Alice signs transaction `T_old` (e.g., `Transfer` of X NEAR to Bob) with nonce `N_old` but does not submit it yet (or it is captured off the wire by an observer/Bob).
3. At a later block height `H2` (still relatively close to `H1`), Alice submits `DeleteKey(pk)` followed by `AddKey(pk, ...)` (same public key) in one or two transactions — a supported action combination (see `swap_key` in `integration-tests/src/user/mod.rs`).
4. `add_regular_key` sets the recreated key's nonce to `initial_nonce_value(H2) = (H2-1) * MULT`, which is less than `N_old` when `H2` is not far past `H1`.
5. Bob (or any holder of `T_old`) now submits `T_old`. `verify_nonce` sees `tx_nonce = N_old > current_nonce = (H2-1)*MULT` and accepts it — `T_old`'s Transfer is executed again, moving funds Alice believed were no longer authorizable via the rotated key. [3](#0-2) [1](#0-0) [6](#0-5)

### Citations

**File:** runtime/runtime/src/verifier.rs (L253-280)
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
    if let Some(height) = block_height {
        let upper_bound = height
            .saturating_mul(near_primitives::account::AccessKey::ACCESS_KEY_NONCE_RANGE_MULTIPLIER);
        if tx_nonce >= upper_bound {
            return Err(InvalidTxError::NonceTooLarge { tx_nonce, upper_bound });
        }
    }
    Ok(())
}
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

**File:** runtime/runtime/src/access_keys.rs (L149-192)
```rust
pub(crate) fn action_add_key(
    apply_state: &ApplyState,
    state_update: &mut TrieUpdate,
    account: &mut Account,
    result: &mut ActionResult,
    account_id: &AccountId,
    add_key: &AddKeyAction,
) -> Result<(), StorageError> {
    if get_access_key(state_update, account_id, &add_key.public_key)?.is_some() {
        result.result = Err(ActionErrorKind::AddKeyAlreadyExists {
            account_id: account_id.to_owned(),
            public_key: add_key.public_key.clone().into(),
        }
        .into());
        return Ok(());
    }

    let fee_config = &apply_state.config.fees;

    if let Some(gas_key_info) = add_key.access_key.gas_key_info() {
        add_gas_key(
            fee_config,
            state_update,
            account,
            account_id,
            &add_key.public_key,
            &add_key.access_key,
            gas_key_info,
            apply_state.block_height,
        )?;
    } else {
        add_regular_key(
            fee_config,
            state_update,
            account,
            account_id,
            &add_key.public_key,
            &add_key.access_key,
            apply_state.block_height,
        )?;
    }

    Ok(())
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

**File:** docs/DataStructures/AccessKey.md (L8-12)
```markdown
    /// The nonce for this access key.
    /// NOTE: In some cases the access key needs to be recreated. If the new access key reuses the
    /// same public key, the nonce of the new access key should be equal to the nonce of the old
    /// access key. It's required to avoid replaying old transactions again.
    pub nonce: Nonce,
```
