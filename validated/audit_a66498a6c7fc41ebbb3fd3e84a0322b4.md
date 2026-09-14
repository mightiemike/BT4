## Analysis

The Solidity bug's core pattern — an "already consumed / already matched" guard that a legitimate self-action can reset to a stale value, re-opening a window for a previously valid but not-yet-executed signed authorization to be replayed — has a direct analog in nearcore's access-key nonce mechanism.

**Root cause path:**

The `AccessKey.nonce` field is nearcore's replay guard: `verify_nonce` requires `tx_nonce > current_nonce` (Monotonic mode) [1](#0-0) . The design intent, stated explicitly in the data-structure docs, is that when an access key with the same public key is recreated, its nonce **must be preserved** to prevent replaying old transactions: *"If the new access key reuses the same public key, the nonce of the new access key should be equal to the nonce of the old access key. It's required to avoid replaying old transactions again."* [2](#0-1) 

However, the actual implementation of `action_delete_key` → `delete_regular_key` simply removes the key from the trie [3](#0-2) , discarding the nonce entirely. `action_add_key` → `add_regular_key` then unconditionally reseeds the new key's nonce from the current block height, with no check for whether this exact public key previously existed with a higher nonce:

```rust
fn add_regular_key(...) {
    let mut access_key = access_key.clone();
    access_key.nonce = initial_nonce_value(block_height); // always overwritten
    ...
}
``` [4](#0-3) 

`initial_nonce_value` is `(block_height - 1) * ACCESS_KEY_NONCE_RANGE_MULTIPLIER` (1,000,000) [5](#0-4) . Since wallets are recommended to pick nonces near `current_height * 1_000_000` specifically to avoid hash collisions on key recreation (per the same doc comment), a `DeleteKey` + `AddKey` of the *same* public key within a single transaction/receipt resets the on-chain nonce guard to a value that can be **lower** than nonces already chosen (and possibly already signed, but not yet submitted) by the account owner under that convention. This exact "delete key then add key with same public key in one receipt" sequence is a supported, exercised code path [6](#0-5) , but that test only checks storage usage, not nonce continuity — the documented anti-replay invariant is not actually verified or enforced anywhere.

This mirrors H-2 precisely: `bulls[orderHash] == address(0)` was meant to signal "already matched," but `transferPosition(0)` could legitimately reset it, letting an old, already-authorized (or here, an old signed-but-unexecuted) message become executable again. Here, `AccessKey.nonce` is meant to signal "already used up to N," but same-key `DeleteKey`+`AddKey` resets it below previously-issued nonce values, re-opening a window (of size up to 1,000,000 nonce values) for any previously signed-but-unsubmitted transaction under that key to become newly executable — a transaction-triggered acceptance of a stale, previously-invalid state transition.

### Title
Access key nonce reset on same-key `DeleteKey`+`AddKey` reopens replay window for previously signed transactions - (File: runtime/runtime/src/access_keys.rs)

### Summary
`action_add_key`'s `add_regular_key` unconditionally reseeds a newly (re)created access key's nonce from the current block height, instead of preserving the nonce of an equal, just-deleted key as the design documentation requires. A single transaction containing `DeleteKey(pk)` followed by `AddKey(pk, ...)` for the same public key resets the account's nonce guard for that key to `(block_height-1) * 1_000_000`, which can be lower than nonces the account already used or previously signed, allowing old signed-but-unexecuted transactions to become valid and executable again.

### Finding Description
`verify_nonce` enforces `tx_nonce > current_nonce` as the sole anti-replay mechanism for a given access key [1](#0-0) . `delete_regular_key` removes the access key row (and its nonce) from the trie with no memory of the value [3](#0-2) . `add_regular_key`, invoked when the same public key is re-added (even within the same transaction/receipt, as exercised by `test_delete_key_add_key` [6](#0-5) ), always sets `access_key.nonce = initial_nonce_value(block_height)` regardless of what nonce the deleted key held [4](#0-3) . The documented invariant that recreated keys must retain the old nonce "to avoid replaying old transactions again" is not implemented for this path [2](#0-1) .

Because wallets are told to derive nonces as roughly `block_height * 1_000_000 + n` precisely to survive key recreation collisions, an account that rotates a key (same public key, e.g. to reset a `FunctionCall` allowance/permission without changing key material, or simply "key hygiene") drops the guard back to `(current_block_height - 1) * 1_000_000` — a value up to ~1,000,000 lower than the nonce it just consumed for the rotating transaction itself, and potentially lower than other nonces the signer already used or pre-signed for future use.

### Impact Explanation
Any previously validly signed transaction from that account/key whose nonce falls in the reopened `[(block_height-1)*1_000_000, current_nonce)` gap becomes acceptable again after the key rotation, even though the signer's intent (and the account's own prior nonce progression) had already advanced past it. This is a transaction-triggered acceptance of a stale, previously-superseded state transition — an old transfer, permission grant, or delegate action can be executed a second time (or for the first time, if it was signed but withheld), causing unauthorized value movement or unauthorized re-execution of an action the signer believed was no longer valid. It maps to the "invalid state transition acceptance" / "unauthorized value movement" impact classes.

### Likelihood Explanation
Reachable by any single unprivileged account via one self-signed transaction containing `[DeleteKey(pk), AddKey(pk, ...)]` for its own key — no special privileges, validator status, or network position required. The prerequisite (an old signed-but-unsubmitted transaction from the same key with a nonce in the reopened range) is a normal consequence of the officially recommended nonce-selection convention (`height * 1_000_000`), making the reopened window realistic for any account/relayer following that guidance rather than a contrived edge case.

### Proof of Concept
1. Account `alice` signs (but does not submit) transaction `T_old` with `nonce = N` where `N ≈ H0 * 1_000_000` at some earlier height `H0`.
2. Later, at height `H1 > H0`, `alice` submits a transaction with actions `[DeleteKey(pk), AddKey(pk, AccessKey::full_access())]` using the same public key `pk`, signed with nonce `N' > N` (consuming/advancing the stored nonce to `N'`).
3. `action_delete_key` removes the key row (with the just-used nonce `N'`); `action_add_key`/`add_regular_key` reseeds the new row's nonce to `initial_nonce_value(H1) = (H1-1) * 1_000_000` [5](#0-4) [4](#0-3) .
4. If `(H1-1) * 1_000_000 < N`, `T_old` (nonce `N`) now satisfies `verify_nonce`'s `tx_nonce > current_nonce` check [1](#0-0)  and can be submitted and executed, even though it should have been permanently superseded by the account's nonce progression to `N'`.


Repository: Thankgoddavid56/nearcore--004

In `runtime/runtime/src/access_keys.rs`, the function `action_add_key` (around line 149) dispatches to `add_regular_key` (line 230) and `add_gas_key` (line 194). Both unconditionally reseed the access key's nonce from `initial_nonce_value(block_height)` without checking whether the same public key existed with a higher nonce immediately prior in the same action_delete_key/action_add_key sequence (or generally at all, since deletion discards the old nonce entirely).

1. Confirm the vulnerability by writing/extending a test in `runtime/runtime/src/tests/apply.rs` (near `test_delete_key_add_key` at line 1268) that:
   - Sets an access key's nonce to a high value via a transaction (e.g., using a nonce close to `block_height * 1_000_000`).
   - In the same or a subsequent receipt, issues `DeleteKey(pk)` followed by `AddKey(pk, ...)` for the identical public key.
   - Asserts that the new key's nonce is lower than the previously consumed nonce, and that a previously-would-be-invalid transaction with a nonce in the gap now passes `verify_nonce`.

2. Fix the issue: when `action_add_key` re-adds a public key, it should look up whether an access key existed under `TrieKey::AccessKey` for that `(account_id, public_key)` immediately prior (this requires either not fully clearing the nonce during delete, or having `action_delete_key`/`action_add_key` cooperate within the same receipt to carry the old nonce forward), and seed the new key's nonce to `max(initial_nonce_value(block_height), old_nonce)` rather than unconditionally overwriting it. This should apply both to `add_regular_key` (`runtime/runtime/src/access_keys.rs:230`) and `add_gas_key` (`runtime/runtime/src/access_keys.rs:194`), consistent with the documented invariant in `docs/DataStructures/AccessKey.md`.

3. Since this changes runtime/protocol-level state-transition behavior, gate the fix behind a new `ProtocolFeature` (see `core/primitives-core/src/version.rs` for the existing feature-gating pattern used by similar runtime fixes like `FixAccessKeyAllowanceCharging`) so that it only activates from a specific protocol version, preserving state-transition determinism/compatibility with un-upgraded nodes.

4. Add regression tests confirming that after the fix, a `DeleteKey`+`AddKey` sequence for the same public key preserves (or takes the max of) the old and newly-seeded nonce, and that previously-superseded transactions remain rejected after key rotation.

5. Run the existing test suite in `runtime/runtime/src/tests/apply.rs` and `test-loop-tests/src/tests/` covering access keys and nonces (e.g. `same_bootstrap_cannot_be_replayed`, `recreated_account_rejects_old_bootstrap`, `test_delete_key_add_key`) to ensure no regressions.

### Citations

**File:** runtime/runtime/src/verifier.rs (L253-270)
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

**File:** runtime/runtime/src/tests/apply.rs (L1268-1311)
```rust
#[test]
fn test_delete_key_add_key() {
    let initial_locked = Balance::from_near(500_000);
    let (runtime, tries, root, apply_state, signers, epoch_info_provider) = setup_runtime(
        vec![alice_account()],
        Balance::from_near(1_000_000),
        initial_locked,
        Gas::from_teragas(1000),
    );

    let state_update = tries.new_trie_update(ShardUId::single_shard(), root);
    let initial_account_state = get_account(&state_update, &alice_account()).unwrap().unwrap();

    let actions = vec![
        Action::DeleteKey(Box::new(DeleteKeyAction { public_key: signers[0].public_key() })),
        Action::AddKey(Box::new(AddKeyAction {
            public_key: signers[0].public_key(),
            access_key: AccessKey::full_access(),
        })),
    ];

    let receipts = vec![create_receipt_with_actions(alice_account(), signers[0].clone(), actions)];

    let apply_result = runtime
        .apply(
            tries.get_trie_for_shard(ShardUId::single_shard(), root),
            &None,
            &apply_state,
            &receipts,
            SignedValidPeriodTransactions::empty(),
            &epoch_info_provider,
            Default::default(),
        )
        .unwrap();
    let mut store_update = tries.store_update();
    let root =
        tries.apply_all(&apply_result.trie_changes, ShardUId::single_shard(), &mut store_update);
    store_update.commit();

    let state_update = tries.new_trie_update(ShardUId::single_shard(), root);
    let final_account_state = get_account(&state_update, &alice_account()).unwrap().unwrap();

    assert_eq!(initial_account_state.storage_usage(), final_account_state.storage_usage());
}
```
