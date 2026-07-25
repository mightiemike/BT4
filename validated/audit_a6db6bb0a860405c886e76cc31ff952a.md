### Title
Cross-Network Replay of `DelegateAction` Signatures Due to Missing Chain Identifier in Signed Payload — (`File: core/primitives/src/action/delegate.rs`)

### Summary

`DelegateAction` (NEAR's meta-transaction primitive, NEP-366) produces a signed payload that contains no chain-specific identifier. A signature produced for NEAR testnet is cryptographically valid on NEAR mainnet (and any other NEAR network) whenever the same account and key pair exist on both networks and the nonce and block-height constraints happen to be satisfiable on the target network. An unprivileged attacker who observes a signed `DelegateAction` on one network can replay it on another, causing the user's inner actions (transfers, function calls, key management, etc.) to execute without the user's consent.

### Finding Description

`DelegateAction` is the struct a user signs to create a meta transaction. Its hash is computed in `get_nep461_hash()`:

```rust
// core/primitives/src/action/delegate.rs
pub fn get_nep461_hash(&self) -> CryptoHash {
    let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
    let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
    hash(&bytes)
}
```

`SignableMessage` prepends a `MessageDiscriminant` — a 4-byte integer (`MIN_ON_CHAIN_DISCRIMINANT + 366 = 2^30 + 366`) — before the serialized `DelegateAction`:

```rust
// core/primitives/src/signable_message.rs
const MIN_ON_CHAIN_DISCRIMINANT: u32 = 1 << 30;
const NEP_366_META_TRANSACTIONS: u32 = 366;
```

The complete signed payload is therefore:

| Field | Value |
|---|---|
| `discriminant` | `u32` = `2^30 + 366` (same on every NEAR network) |
| `sender_id` | `AccountId` |
| `receiver_id` | `AccountId` |
| `actions` | `Vec<NonDelegateAction>` |
| `nonce` | `u64` |
| `max_block_height` | `u64` |
| `public_key` | `PublicKey` |

**No `chain_id`, no `block_hash`, no network-specific byte appears anywhere in this payload.**

By contrast, a regular `SignedTransaction` (both V0 and V1) includes a `block_hash` field that binds the transaction to a specific chain, because a block hash from mainnet cannot exist on testnet:

```rust
// core/primitives/src/transaction.rs
pub struct TransactionV1 {
    pub signer_id: AccountId,
    pub public_key: PublicKey,
    pub nonce: TransactionNonce,
    pub receiver_id: AccountId,
    pub block_hash: CryptoHash,   // ← chain-specific binding
    pub actions: Vec<Action>,
    pub nonce_mode: NonceMode,
}
```

`DelegateAction` has no equivalent binding. Its only time-bounding field is `max_block_height`, which is a plain integer that can be satisfied on any NEAR network independently.

The runtime validates the signature in `apply_delegate_action` → `validate_delegate_action_key` in `runtime/runtime/src/actions.rs`. The checks performed are:

1. Access key exists for `(sender_id, public_key)`.
2. `delegate_nonce > current_nonce` (monotonic) or `== current_nonce + 1` (strict).
3. `delegate_nonce < block_height * ACCESS_KEY_NONCE_RANGE_MULTIPLIER`.
4. `current_block_height < max_block_height`.
5. Signature verifies against `get_nep461_hash()`.

None of these checks involve the network identity.

### Impact Explanation

An attacker who observes a `SignedDelegateAction` on network A (e.g., testnet) can submit the identical bytes to network B (e.g., mainnet) via any relayer or directly in a transaction. If the victim's account exists on network B with the same key pair, and the nonce and block-height constraints are satisfiable on network B, the inner actions execute with `sender_id` as the predecessor — exactly as if the victim had authorized them on network B.

Inner actions that can be replayed include:
- `Transfer` — drains NEAR balance from the victim on the unintended network.
- `FunctionCall` — executes arbitrary contract logic (e.g., DeFi withdrawals, NFT transfers) on the unintended network.
- `AddKey` / `DeleteKey` — modifies the victim's key set on the unintended network.
- `DeleteAccount` — destroys the victim's account on the unintended network.

This satisfies the **unauthorized transaction** and **balance manipulation** impact categories. The relayer pays gas, so the victim does not even need to hold NEAR on the target network for the replay to succeed.

### Likelihood Explanation

The preconditions are realistic:

1. **Same account on both networks**: NEAR account IDs are human-readable strings. Developers and users routinely create the same account name on both mainnet and testnet. Implicit accounts (derived from a public key) are identical across networks by construction.
2. **Same key pair**: Developers frequently reuse the same key pair across networks. Implicit accounts always derive their key from the account ID, making the key identical on every network.
3. **Nonce satisfiable on target network**: A freshly created account on mainnet has nonce 0. Any testnet delegate action with nonce ≥ 1 satisfies the monotonic check. Even for accounts with activity, the nonce spaces diverge independently, so a testnet nonce is often valid on mainnet.
4. **`max_block_height` not expired**: Relayers typically set `max_block_height` to hundreds or thousands of blocks in the future. Mainnet and testnet block heights are in the same order of magnitude, so a height valid on testnet is usually also in the future on mainnet.
5. **Attacker can observe the signed payload**: `SignedDelegateAction` bytes are visible in on-chain transaction data and are transmitted off-chain to relayers. Any observer of either channel can extract and replay them.

### Recommendation

Include the network's `chain_id` in the signed payload. The most backward-compatible approach is to add it to the `MessageDiscriminant` or to the `DelegateAction` struct itself, and to enforce it during signature verification in `apply_delegate_action`.

A minimal fix adds `chain_id: String` to `DelegateAction` (and `DelegateActionV2`) and verifies it matches `apply_state.config.chain_id` (or equivalent) before accepting the signature. Alternatively, the `SignableMessage` scheme can be extended to include the chain ID as a mandatory prefix field, so all future signable message types inherit the protection automatically.

Regular `SignedTransaction` should be used as the reference: it achieves chain binding via `block_hash`. `DelegateAction` needs an equivalent.

### Proof of Concept

**Setup:**
- Alice has account `alice.near` on both mainnet and testnet, with the same ED25519 key pair.
- Alice's mainnet access key nonce is 0; testnet nonce is 5.

**Step 1 — Alice signs a delegate action on testnet:**
```rust
let delegate_action = DelegateAction {
    sender_id: "alice.near".parse().unwrap(),
    receiver_id: "bob.near".parse().unwrap(),
    actions: vec![transfer_100_near],
    nonce: 6,                    // valid on testnet (> 5)
    max_block_height: 200_000_000, // far future on both networks
    public_key: alice_key.public_key(),
};
let hash = delegate_action.get_nep461_hash(); // no chain_id in hash
let signature = alice_key.sign(hash.as_bytes());
let signed = SignedDelegateAction { delegate_action, signature };
```

**Step 2 — Attacker observes `signed` on testnet (on-chain or via relayer).**

**Step 3 — Attacker wraps it in a mainnet transaction:**
```rust
// Attacker submits to mainnet RPC
let mainnet_tx = SignedTransaction::from_actions(
    attacker_nonce,
    attacker_account,
    "alice.near".parse().unwrap(), // outer receiver = delegate sender
    &attacker_signer,
    vec![Action::Delegate(Box::new(signed))], // same bytes, different network
    mainnet_block_hash,
);
```

**Step 4 — Mainnet runtime executes `apply_delegate_action`:**
- Access key found for `(alice.near, alice_key)` ✓
- Nonce check: `6 > 0` ✓
- Block height check: `200_000_000 > current_mainnet_height` ✓
- Signature check: `get_nep461_hash()` produces the same hash (no chain_id) ✓
- **Result:** 100 NEAR transferred from Alice on mainnet without her consent.

The root cause is in `get_nep461_hash()` at `core/primitives/src/action/delegate.rs` line 353–357, which hashes only the `MessageDiscriminant` (a protocol-type tag, not a network tag) and the `DelegateAction` body, neither of which contains any chain-specific data. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** core/primitives/src/action/delegate.rs (L349-357)
```rust
    /// Delegate action hash used for NEP-461 signature scheme which tags
    /// different messages before hashing
    ///
    /// For more details, see: [NEP-461](https://github.com/near/NEPs/pull/461)
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

**File:** core/primitives/src/signable_message.rs (L97-108)
```rust
impl<'a, T: BorshSerialize> SignableMessage<'a, T> {
    pub fn new(msg: &'a T, ty: SignableMessageType) -> Self {
        let discriminant = ty.into();
        Self { discriminant, msg }
    }

    pub fn sign(&self, signer: &Signer) -> Signature {
        let bytes = borsh::to_vec(&self).expect("Failed to deserialize");
        let hash = hash(&bytes);
        signer.sign(hash.as_bytes())
    }
}
```

**File:** core/primitives/src/transaction.rs (L118-137)
```rust
#[derive(BorshSerialize, BorshDeserialize, PartialEq, Eq, Debug, Clone, ProtocolSchema)]
pub struct TransactionV1 {
    /// An account on which behalf transaction is signed
    pub signer_id: AccountId,
    /// A public key of the access key which was used to sign an account.
    /// Access key holds permissions for calling certain kinds of actions.
    pub public_key: PublicKey,
    /// Nonce is used to determine order of transaction in the pool.
    /// It increments for a combination of `signer_id` and `public_key`,
    /// and for gas key it also includes a `nonce_index`.
    pub nonce: TransactionNonce,
    /// Receiver account for this transaction
    pub receiver_id: AccountId,
    /// The hash of the block in the blockchain on top of which the given transaction is valid
    pub block_hash: CryptoHash,
    /// A list of actions to be applied
    pub actions: Vec<Action>,
    /// Controls nonce validation mode (monotonic or strict sequential).
    pub nonce_mode: NonceMode,
}
```

**File:** runtime/runtime/src/actions.rs (L530-622)
```rust
/// Validate access key which was used for signing DelegateAction:
///
/// - Checks whether the access key is present fo given public_key and sender_id.
/// - Validates nonce and updates it if it's ok.
/// - Validates access key permissions.
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
