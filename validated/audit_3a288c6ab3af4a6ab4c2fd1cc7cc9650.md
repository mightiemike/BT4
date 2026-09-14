## Analog Found

### Title
Access-key rotation (`DeleteKey` + `AddKey` reusing the same public key) does not preserve the nonce, allowing replay of pre-signed transactions after "key rotation" - ([File: runtime/runtime/src/access_keys.rs])

### Summary
The ArangoDB CVE describes a session that is not invalidated when a password is changed, letting an attacker who already holds a valid session keep acting as the user. The nearcore analog is an access key's "session" state — its `nonce` — which the protocol documentation explicitly says must be carried over when an access key is recreated with the same public key, specifically to prevent replay of old signed transactions. The actual implementation ignores that invariant and resets the nonce, which reopens the replay window the design was meant to close.

### Finding Description
`docs/DataStructures/AccessKey.md` states the intended invariant explicitly:

> "NOTE: In some cases the access key needs to be recreated. If the new access key reuses the same public key, the nonce of the new access key should be equal to the nonce of the old access key. It's required to avoid replaying old transactions again." [1](#0-0) 

This is a documented, sanctioned flow: NEAR's own docs tell users that to change a `FunctionCallPermission` allowance, they must delete the old key and add a new one with the same public key — i.e., `swap_key`. [2](#0-1) [3](#0-2) 

However, `action_delete_key` deletes the key with no memory of its prior nonce: [4](#0-3) 

And `add_regular_key` unconditionally re-seeds the nonce from the current block height, discarding whatever nonce the old key had reached: [5](#0-4) [6](#0-5) 

The nonce range design intentionally allows nonces up to `block_height * ACCESS_KEY_NONCE_RANGE_MULTIPLIER (1_000_000)` so that clients can pre-sign transactions with future nonces without re-querying state: [7](#0-6) 

Because the new key's nonce floor is `(block_height-1)*1_000_000`, and an old key could legitimately have reached a nonce anywhere up to `block_height*1_000_000 - 1` before rotation, any transaction pre-signed with a nonce in that gap remains valid against the freshly recreated key: `verify_nonce` only requires `tx_nonce > current_nonce` and `tx_nonce < block_height*1_000_000`, both of which are satisfied. There is a pre-existing acknowledgement of this class of bug in the test suite itself: [8](#0-7) 

This nonce reuse gap also affects the meta-transaction (`DelegateAction`) path, which authorizes against the same `access_key.nonce`: [9](#0-8) 

### Impact Explanation
A user (or a relaying dApp on the user's behalf) who rotates an access key — the NEAR equivalent of changing a password/session credential, e.g. to revoke a leaked signature, change allowance, or respond to suspected key compromise — cannot actually invalidate transactions that were signed under the old key with a nonce inside the un-consumed range. An adversary holding such a pre-signed transaction (e.g., obtained via a compromised client, a malicious dApp that convinced the user to pre-sign a batch of "session" transactions, or a meta-transaction relayer) can submit it *after* the legitimate key rotation and have it accepted and executed as if the old authorization were still valid. This can result in unauthorized value transfers, unauthorized `FunctionCall` invocations, or unauthorized permission grants (e.g., adding an attacker-controlled `FullAccess` key) executed under an account that believed its "session" (access key) had been invalidated.

### Likelihood Explanation
The `DeleteKey` + `AddKey` (same public key) pattern is explicitly documented as the supported way to change an access key's permissions/allowance, so it is a realistic, reachable action for any account owner or any application that manages access keys programmatically (e.g., relayers, session-key wallets). Nonce pre-signing beyond the immediately-next nonce is also an explicitly supported client pattern (that's the entire purpose of `ACCESS_KEY_NONCE_RANGE_MULTIPLIER`), so an attacker who has captured even one such pre-signed transaction (via phishing, a malicious webpage prompting "session" signatures, or a compromised relayer) has a straightforward path to replay it post-rotation.

### Recommendation
When `AddKeyAction` is used to recreate an access key with a public key that just had (or still has, within the same transaction) an `AccessKey` record for the same `account_id`/`public_key`, the new key's `nonce` should be seeded from the maximum of the old key's nonce and the current block-height floor, not unconditionally reset from `initial_nonce_value(block_height)`. This restores the invariant already documented in `docs/DataStructures/AccessKey.md` and closes the replay window between rotations.

### Proof of Concept
1. Account `alice.near` has a `FullAccess` key `pk` created at block height `h0`; its stored nonce floor is `(h0-1)*1_000_000`.
2. Alice's wallet (or a relayer she authorized) pre-signs a sensitive transaction `TX_evil` under `pk` with `nonce = (h0-1)*1_000_000 + 500_000` (a legitimate future nonce per the supported nonce-range scheme) — e.g., `AddKey(attacker_pubkey, FullAccess)`. This transaction is captured by an attacker (leaked client, malicious dApp, compromised relayer) but not yet submitted.
3. Believing the key may be compromised, Alice submits a rotation transaction with actions `[DeleteKey(pk), AddKey(pk, new_access_key)]` at block height `h1` (`h1` close to `h0`). Per `action_delete_key`/`add_regular_key`, the new key's nonce is reset to `(h1-1)*1_000_000`, which is lower than `500_000` above the old floor.
4. The attacker submits the previously captured `TX_evil`. `verify_nonce` accepts it because `tx_nonce > current_nonce` (new low floor) and `tx_nonce < block_height*1_000_000` (upper bound, still satisfied at any later height).
5. `TX_evil` executes under the "rotated" key, granting the attacker a `FullAccess` key on Alice's account despite the rotation — demonstrating the session (access key) was not actually invalidated, matching the CVE's "insufficient session expiration after credential change" bug class.

### Citations

**File:** docs/DataStructures/AccessKey.md (L8-12)
```markdown
    /// The nonce for this access key.
    /// NOTE: In some cases the access key needs to be recreated. If the new access key reuses the
    /// same public key, the nonce of the new access key should be equal to the nonce of the old
    /// access key. It's required to avoid replaying old transactions again.
    pub nonce: Nonce,
```

**File:** docs/DataStructures/AccessKey.md (L36-39)
```markdown
    /// `None` means unlimited allowance.
    /// NOTE: To change or increase the allowance, the old access key needs to be deleted and a new
    /// access key should be created.
    pub allowance: Option<Balance>,
```

**File:** integration-tests/src/user/mod.rs (L232-247)
```rust
    fn swap_key(
        &self,
        signer_id: AccountId,
        old_public_key: PublicKey,
        new_public_key: PublicKey,
        access_key: AccessKey,
    ) -> Result<FinalExecutionOutcomeView, CommitError> {
        self.sign_and_commit_actions(
            signer_id.clone(),
            signer_id,
            vec![
                Action::DeleteKey(Box::new(DeleteKeyAction { public_key: old_public_key })),
                Action::AddKey(Box::new(AddKeyAction { public_key: new_public_key, access_key })),
            ],
        )
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

**File:** runtime/runtime/src/verifier.rs (L210-237)
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

**File:** integration-tests/src/tests/standard_cases/mod.rs (L1159-1163)
```rust
        Err(err) => {
            // TODO(#6724): This is a wrong error, the transaction actually
            // succeeds. We get an error here when we retry the tx and the second
            // time around it fails. Normally, retries are handled by nonces, but we
            // forget the nonce when we delete a key!
```

**File:** runtime/runtime/src/actions.rs (L561-571)
```rust
    let delegate_nonce = delegate_action.nonce();
    let (current_nonce, nonce_update) = match delegate_nonce {
        TransactionNonce::Nonce { .. } => {
            if access_key.gas_key_info().is_some() {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DelegateActionRequiresNonGasKey,
                )
                .into());
                return Ok(());
            }
            (access_key.nonce, DelegateNonceUpdate::AccessKey)
```
