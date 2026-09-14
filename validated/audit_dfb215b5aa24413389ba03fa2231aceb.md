### Title
Access-key nonce reset on same-pubkey re-issuance allows replay of previously-signed transactions after key rotation - (File: `runtime/runtime/src/access_keys.rs`)

### Summary
When an account owner rotates an access key by deleting it and re-adding a key with the **same public key** (the documented pattern for changing a `FunctionCall` allowance/permission, per `docs/DataStructures/AccessKey.md`), the new key's nonce is unconditionally reset to a block-height-derived value rather than being carried forward from the old key. Because a signer is free to pre-sign a transaction with any nonce up to `current_block_height * 1_000_000`, a previously valid but not-yet-submitted signed transaction can remain valid (nonce still greater than the reset nonce) and be replayed against the new key state — exactly the "reusable token after reset" pattern described in the reference advisory.

### Finding Description
`AccessKey` documents an explicit anti-replay invariant: [1](#0-0) 

and the same guarantee is spelled out in the design docs: [2](#0-1) 

However, the actual re-issuance path in `add_regular_key` does not honor this: it always sets the new nonce to a value derived purely from the current block height, with no reference to the nonce of the key being replaced: [3](#0-2) 

and the helper computing that seed value: [4](#0-3) 

Meanwhile, `verify_nonce` only requires the submitted nonce to (a) exceed the access key's *current* stored nonce and (b) stay below `current_block_height * ACCESS_KEY_NONCE_RANGE_MULTIPLIER` — a bound tied to the block at *submission* time, not at key-creation time: [5](#0-4) 

This means a user can legitimately pre-sign a transaction with a nonce close to the current upper bound (e.g. `block_height * 1_000_000 - 1`) while the key is still active, hold it unsent, then delete and re-add the *same public key* (e.g., to change the `FunctionCallPermission` allowance/receiver/method restrictions, as explicitly sanctioned by the documented workflow). Because `DeleteKey` followed by `AddKey` for the same public key executes within the same receipt at the same `apply_state.block_height`, the freshly reset nonce (`(block_height-1)*1_000_000`) is lower than the nonce already used in the pre-signed transaction. That old transaction therefore remains admissible under the new key and can be broadcast/replayed later, even though the owner's intent in rotating the key was to invalidate prior signing capability/permissions tied to that key material.

### Impact Explanation
This breaks the invariant that key rotation (delete + re-add with the same public key) invalidates previously signed but unexecuted transactions. In a legitimate rotation scenario — e.g., downgrading allowance or changing the receiver/method restriction on a `FunctionCall` key while retaining the same public key — an attacker (or the key owner making a mistake) holding a stale, high-nonce, pre-signed transaction authorized under the *old* permissions can still get it accepted and executed under the *new* key record, as long as the action is compatible with the new permission set (e.g., both are `FullAccess`, or the stale transaction happens to also satisfy the new `FunctionCall` restriction). This can result in unauthorized/unintended value transfers or action execution that the owner believed had been revoked by the key rotation — a concrete unauthorized-value-movement / invalid-state-transition-acceptance class of impact, directly analogous to the reused password-reset-token vulnerability in the referenced advisory.

### Likelihood Explanation
Exploitability requires the account owner to have pre-signed a transaction with an unusually large nonce (near the current upper bound) before performing a same-public-key key rotation — a documented, legitimate operation, not an attacker-controlled network condition, private-key leak, or validator misbehavior. This is a normal application-layer pattern (e.g., relayers/wallets pre-signing several transactions for later dispatch, then adjusting key permissions). No malicious peer/validator/node involvement is required — only a submitted transaction (the stale pre-signed one) and a normal `DeleteKey`+`AddKey` rotation reachable from any account holder's own transactions.

### Recommendation
When re-adding an access key with a public key that previously existed on the account, preserve or take the maximum of the old key's nonce (across regular and gas-key nonce rows) and the block-height-derived seed, rather than unconditionally resetting to `initial_nonce_value(block_height)`. This restores the invariant documented in `AccessKey`'s doc comment and in `docs/DataStructures/AccessKey.md`, and closes the replay window created by nonce reset on same-pubkey rotation.

### Proof of Concept
1. Alice's account has full-access key `K` with `AccessKey.nonce = N0` at block height `H0`.
2. While `K` is active, Alice signs (but does not submit) transaction `T` with `nonce = H0 * 1_000_000 - 1` (a large, currently-valid nonce, satisfying `verify_nonce`'s upper bound for the current block height) and holds it.
3. Later, at block height `H1 > H0`, Alice submits a transaction containing `[DeleteKey(K), AddKey(K, new_permission)]` to rotate `K` to a different (e.g., more restricted) permission set while keeping the same public key — a legitimate documented flow.
4. `action_delete_key` removes the old record; `action_add_key` → `add_regular_key` resets `AccessKey.nonce = initial_nonce_value(H1) = (H1-1)*1_000_000` (`runtime/runtime/src/access_keys.rs:240`), which is lower than `T`'s nonce as long as `H1*1_000_000 - 1_000_000 < H0*1_000_000 - 1`, i.e., whenever `H1` is not drastically larger than `H0` (easily arranged, e.g. rotation performed shortly after signing `T`).
5. Alice (or anyone holding `T`) later submits `T`. `verify_nonce` accepts it because `T.nonce > current AccessKey.nonce` and `T.nonce < current_block_height * 1_000_000`. `T` executes under the new key record, even though Alice intended the rotation to supersede/limit what could be executed with that key.

### Citations

**File:** core/primitives-core/src/account.rs (L468-471)
```rust
    /// Nonce for this access key, used for tx nonce generation. When access key is created, nonce
    /// is set to `(block_height - 1) * 1e6` to avoid tx hash collision on access key re-creation.
    /// See <https://github.com/near/nearcore/issues/3779> for more details.
    pub nonce: Nonce,
```

**File:** docs/DataStructures/AccessKey.md (L8-16)
```markdown
    /// The nonce for this access key.
    /// NOTE: In some cases the access key needs to be recreated. If the new access key reuses the
    /// same public key, the nonce of the new access key should be equal to the nonce of the old
    /// access key. It's required to avoid replaying old transactions again.
    pub nonce: Nonce,
    /// Defines permissions for this access key.
    pub permission: AccessKeyPermission,
}
```
```

**File:** runtime/runtime/src/access_keys.rs (L46-50)
```rust
pub(crate) fn initial_nonce_value(block_height: BlockHeight) -> Nonce {
    // Set default nonce for newly created access key to avoid transaction hash collision.
    // See <https://github.com/near/nearcore/issues/3779>.
    (block_height - 1) * near_primitives::account::AccessKey::ACCESS_KEY_NONCE_RANGE_MULTIPLIER
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

**File:** runtime/runtime/src/verifier.rs (L210-236)
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
```
