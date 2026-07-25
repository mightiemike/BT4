### Title
Cross-Chain Replay of `SignedDelegateAction` Due to Missing Chain Domain Separator in `get_nep461_hash` — (`File: core/primitives/src/action/delegate.rs`, `core/primitives/src/signable_message.rs`)

---

### Summary

The `DelegateAction` (meta-transaction) signing hash produced by `get_nep461_hash()` contains no chain-specific identifier — no genesis hash, no chain ID, no network name. The `SignableMessage` discriminant is a fixed compile-time constant (`2^30 + 366`). A `SignedDelegateAction` validly signed for NEAR mainnet is cryptographically identical on NEAR testnet (or any other NEAR-protocol chain) if the same account name and public key exist there with a valid nonce. A malicious relayer — an unprivileged role — can intercept a signed delegate action intended for one chain and submit it on another, executing the inner actions without the user's consent on the target chain.

---

### Finding Description

`DelegateAction.get_nep461_hash()` constructs the signed payload as:

```
sha256( borsh( { discriminant: 2^30 + 366,  msg: delegate_action } ) )
``` [1](#0-0) 

The `SignableMessage` struct serialized here contains only a fixed NEP-number discriminant and the action body: [2](#0-1) 

The discriminant is a constant derived solely from the NEP number — it carries no chain identity: [3](#0-2) 

The `DelegateAction` payload itself contains `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key`: [4](#0-3) 

None of these fields are chain-specific. `max_block_height` is a bare integer (not a block hash), so it provides only temporal expiry, not chain binding.

By contrast, a regular `SignedTransaction` includes `block_hash` in its borsh-serialized body, which is a hash of a specific block on a specific chain: [5](#0-4) 

This makes regular transaction signatures chain-specific. `DelegateAction` signatures are not.

`apply_delegate_action` — the sole on-chain validation entry point — checks signature validity, temporal expiry, sender match, nonce, and access-key permissions, but performs no chain-identity check: [6](#0-5) 

`validate_delegate_action_key` similarly checks nonce bounds and access-key permissions with no chain-domain guard: [7](#0-6) 

---

### Impact Explanation

A malicious relayer (unprivileged) who receives a `SignedDelegateAction` from a user targeting NEAR mainnet can submit the identical struct on NEAR testnet (or any NEAR-protocol fork). If the user's account name and public key exist on the target chain with a nonce lower than the one in the signed action, the runtime will:

1. Accept the signature (`verify()` passes — the hash is identical on both chains).
2. Accept the nonce (it is strictly greater than the current access-key nonce on the target chain).
3. Accept `max_block_height` (the target chain's block height may be below the limit).
4. Execute the inner actions with `sender_id` as predecessor.

If the inner actions include a `Transfer` or a `FunctionCall` that moves tokens (e.g., `ft_transfer`), the user loses funds on the target chain without having authorized that chain's execution. The broken invariant is: **a `SignedDelegateAction` must only be executable on the chain for which it was signed**.

Allowed impacts matched: **unauthorized transaction**, **stealing or loss of funds**.

---

### Likelihood Explanation

- NEAR mainnet and testnet share the same account-ID namespace; a user with `alice.near` on mainnet typically also has `alice.near` on testnet.
- Many users and wallets reuse the same Ed25519 key pair across networks during development or because their wallet generates one key per account name.
- The relayer role is unprivileged — anyone can operate a relayer. A malicious relayer is a standard attacker in the meta-transaction threat model (the docs explicitly note relayer trust assumptions).
- The nonce condition is satisfied whenever the user has not yet used that nonce on the target chain, which is the common case for a freshly signed action.

---

### Recommendation

Include a chain-domain separator in the `SignableMessage` signed by `DelegateAction`. The natural choice is the genesis block hash (already used as the `GenesisId.hash` in the network protocol), which uniquely identifies each NEAR chain:

```rust
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub genesis_hash: CryptoHash,   // add this
    pub msg: &'a T,
}
```

`get_nep461_hash` would accept the genesis hash as a parameter and include it in the borsh-serialized payload before hashing. The runtime's `apply_delegate_action` would pass `apply_state.genesis_hash` (already available in `ApplyState` or derivable from chain config) when calling `verify`. This mirrors how EIP-712 includes `chainId` and `verifyingContract` in the domain separator, and how regular NEAR transactions bind to a specific chain via `block_hash`.

This is a protocol-level change requiring a new protocol version gate.

---

### Proof of Concept

1. Alice signs a `DelegateAction` on mainnet:
   - `sender_id = "alice.near"`, `receiver_id = "ft.near"`, `actions = [ft_transfer("eve", 1000)]`, `nonce = 1_000_001`, `max_block_height = 200_000_000`, `public_key = alice_ed25519_key`
   - Hash = `sha256(borsh({discriminant: 0x40000000 + 366, msg: delegate_action}))` — **no mainnet identifier**

2. Alice sends the `SignedDelegateAction` to a relayer off-chain.

3. The malicious relayer ignores mainnet and instead wraps the identical `SignedDelegateAction` in a testnet transaction:
   ```
   SignedTransaction {
     signer_id: relayer,
     receiver_id: "alice.near",   // testnet
     actions: [Action::Delegate(signed_delegate_action)],
     block_hash: <testnet recent block>,
     nonce: relayer_nonce,
   }
   ```

4. Testnet runtime calls `apply_delegate_action`:
   - `signed_delegate_action.verify()` → **passes** (hash is identical on testnet)
   - `block_height <= max_block_height` → **passes** (testnet height < 200_000_000)
   - `sender_id == "alice.near"` → **passes**
   - `access_key nonce < 1_000_001` → **passes** (Alice's testnet key nonce is 0)

5. `ft_transfer("eve", 1000)` executes on testnet's `ft.near` contract, draining Alice's testnet FT balance without her consent.

### Citations

**File:** core/primitives/src/action/delegate.rs (L46-64)
```rust
pub struct DelegateAction {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    ///
    /// With the meta transactions MVP defined in NEP-366, nested
    /// DelegateActions are not allowed. A separate type is used to enforce it.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce to ensure that the same delegate action is not sent twice by a
    /// relayer and should match for given account's `public_key`.
    /// After this action is processed it will increment.
    pub nonce: Nonce,
    /// The maximal height of the block in the blockchain below which the given DelegateAction is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

**File:** core/primitives/src/action/delegate.rs (L353-357)
```rust
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
        let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
        hash(&bytes)
    }
```

**File:** core/primitives/src/signable_message.rs (L18-25)
```rust
const MIN_ON_CHAIN_DISCRIMINANT: u32 = 1 << 30;
const MAX_ON_CHAIN_DISCRIMINANT: u32 = (1 << 31) - 1;
const MIN_OFF_CHAIN_DISCRIMINANT: u32 = 1 << 31;
const MAX_OFF_CHAIN_DISCRIMINANT: u32 = u32::MAX;

// NEPs currently included in the scheme
const NEP_366_META_TRANSACTIONS: u32 = 366;
const NEP_611_GAS_KEYS: u32 = 611;
```

**File:** core/primitives/src/signable_message.rs (L61-65)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}
```

**File:** core/primitives/src/transaction.rs (L130-133)
```rust
    pub receiver_id: AccountId,
    /// The hash of the block in the blockchain on top of which the given transaction is valid
    pub block_hash: CryptoHash,
    /// A list of actions to be applied
```

**File:** runtime/runtime/src/actions.rs (L422-451)
```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    if !signed_delegate_action.verify() {
        result.result = Err(ActionErrorKind::DelegateActionInvalidSignature.into());
        return Ok(());
    }
    let delegate_action = signed_delegate_action.delegate_action();
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
    if delegate_action.sender_id().as_str() != sender_id.as_str() {
        result.result = Err(ActionErrorKind::DelegateActionSenderDoesNotMatchTxReceiver {
            sender_id: delegate_action.sender_id().clone(),
            receiver_id: sender_id.clone(),
        }
        .into());
        return Ok(());
    }

    validate_delegate_action_key(state_update, apply_state, delegate_action, result)?;
    if result.result.is_err() {
        // Validation failed. Need to return Ok() because this is not a runtime error.
        // "result.result" will be return to the User as the action execution result.
```

**File:** runtime/runtime/src/actions.rs (L535-622)
```rust
fn validate_delegate_action_key(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    delegate_action: VersionedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let sender_id = delegate_action.sender_id();
    let public_key = delegate_action.public_key();
    // 'sender_id' account existence must be checked by a caller
    let mut access_key = match get_access_key(state_update, sender_id, public_key)? {
        Some(access_key) => access_key,
        None => {
            result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                InvalidAccessKeyError::AccessKeyNotFound {
                    account_id: sender_id.clone(),
                    public_key: public_key.clone().into(),
                },
            )
            .into());
            return Ok(());
        }
    };

    // A plain nonce advances the single access_key.nonce and forbids gas keys;
    // a gas key nonce advances one of the gas key's nonces selected by
    // nonce_index.
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
        }
        TransactionNonce::GasKeyNonce { nonce_index, .. } => {
            let Some(gas_key_info) = access_key.gas_key_info() else {
                result.result = Err(ActionErrorKind::DelegateActionAccessKeyError(
                    InvalidAccessKeyError::DelegateActionRequiresGasKey,
                )
                .into());
                return Ok(());
            };
            if nonce_index >= gas_key_info.num_nonces {
                result.result = Err(ActionErrorKind::DelegateActionInvalidNonceIndex {
                    nonce_index,
                    num_nonces: gas_key_info.num_nonces,
                }
                .into());
                return Ok(());
            }
            // The index is range-checked above and gas keys initialize every
            // nonce row at creation, so a missing row is inconsistent state.
            let current_nonce =
                get_gas_key_nonce(state_update, sender_id, public_key, nonce_index)?.ok_or_else(
                    || {
                        StorageError::StorageInconsistentState(format!(
                            "gas key nonce row missing for {} {} at in-range index {nonce_index} (num_nonces {})",
                            sender_id, public_key, gas_key_info.num_nonces,
                        ))
                    },
                )?;
            (current_nonce, DelegateNonceUpdate::GasKey { nonce_index })
        }
    };

    if delegate_nonce.nonce() <= current_nonce {
        result.result = Err(ActionErrorKind::DelegateActionInvalidNonce {
            delegate_nonce: delegate_nonce.nonce(),
            ak_nonce: current_nonce,
        }
        .into());
        return Ok(());
    }

    let upper_bound = apply_state.block_height
        * near_primitives::account::AccessKey::ACCESS_KEY_NONCE_RANGE_MULTIPLIER;
    if delegate_nonce.nonce() >= upper_bound {
        result.result = Err(ActionErrorKind::DelegateActionNonceTooLarge {
            delegate_nonce: delegate_nonce.nonce(),
            upper_bound,
        }
        .into());
        return Ok(());
    }
```
