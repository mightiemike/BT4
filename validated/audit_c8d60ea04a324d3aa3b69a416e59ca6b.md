### Title
Cross-Network Replay of Signed `DelegateAction` (Meta-Transaction) Due to Missing Chain Identifier in Signed Payload — (`File: core/primitives/src/action/delegate.rs`)

---

### Summary

`DelegateAction` (NEP-366 meta-transactions) and `DelegateActionV2` (NEP-611 gas-key meta-transactions) produce a signed hash that contains no chain identifier. The `SignableMessage` discriminant is only a message-type tag (a NEP number), not a network-specific binding. A signed `DelegateAction` produced on one NEAR network (e.g., mainnet) is cryptographically valid on any other NEAR network (e.g., testnet, localnet) where the same account, key, and nonce conditions are satisfied, enabling a malicious relayer to replay the user's authorization on an unintended network.

---

### Finding Description

`DelegateAction.get_nep461_hash()` constructs the signed payload as:

```
SHA-256( borsh( MessageDiscriminant(NEP-366) || DelegateAction ) )
``` [1](#0-0) 

The `DelegateAction` struct contains `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key` — but **no chain ID, no genesis hash, and no block hash**. [2](#0-1) 

The `SignableMessage` discriminant is a fixed 32-bit integer derived from the NEP number (e.g., `2^30 + 366 = 1073742190`). It distinguishes message types (`DelegateAction` vs `DelegateActionV2`) but carries no network identity. [3](#0-2) [4](#0-3) 

`DelegateActionV2` (`VersionedDelegateActionPayload::get_nep461_hash`) has the identical omission: [5](#0-4) 

`apply_delegate_action` — the runtime enforcement point — checks only:
1. Signature validity against the chain-ID-free hash
2. `block_height > max_block_height` (a plain integer, not chain-specific)
3. `sender_id` match
4. Nonce > current access-key nonce [6](#0-5) 

None of these checks bind the action to a specific NEAR network.

By contrast, regular `SignedTransaction` is implicitly chain-bound because its hash covers a `block_hash` field — a hash of a specific block that only exists on one chain's history — and the runtime rejects transactions whose `block_hash` is not found within `transaction_validity_period` blocks. [7](#0-6) 

`DelegateAction` has no equivalent binding.

---

### Impact Explanation

A malicious relayer who receives a user's signed `DelegateAction` (e.g., a transfer of tokens on mainnet) can submit the identical signed struct on any other NEAR network where:

- The same `sender_id` account exists with the same `public_key` registered.
- The access-key nonce on the target network is strictly less than the `nonce` in the delegate action.
- The target network's current block height is less than `max_block_height`.

All three conditions are routinely satisfied: developers commonly use the same keys on mainnet and testnet; testnet is periodically reset (resetting nonces); and `max_block_height` is a plain integer with no chain binding.

The executed inner `actions` (e.g., `Transfer`, `FunctionCall`, `AddKey`) run with `sender_id` as the predecessor, causing unauthorized token transfers or state mutations on the target network under the user's identity without their consent for that network.

This is an **unauthorized transaction** impact, which is within the stated HackenProof scope.

---

### Likelihood Explanation

- **Relayer access**: The relayer is the direct recipient of the signed `DelegateAction` by design; no interception is required.
- **Same-key accounts**: Using the same ED25519 key on mainnet and testnet is standard developer practice.
- **Nonce condition**: Testnet nonces are frequently lower than mainnet nonces, especially after testnet resets.
- **`max_block_height` condition**: Block heights on mainnet and testnet are independent counters; a mainnet `max_block_height` of, say, 130,000,000 is also a valid future height on testnet.

The attack requires a malicious or compromised relayer, which is the exact threat model meta-transactions are designed to tolerate for gas payment — but not for cross-network execution.

---

### Recommendation

Include the chain identifier in the signed payload. The genesis hash (`GenesisId.hash`) or the string `chain_id` (e.g., `"mainnet"`, `"testnet"`) should be incorporated into `DelegateAction`'s hashed bytes, analogous to how EIP-155 binds Ethereum transactions to a chain ID.

Concretely, add a `chain_id: String` (or `genesis_hash: CryptoHash`) field to `DelegateAction` and `DelegateActionV2`, or extend `SignableMessage` to include a network discriminant alongside the message-type discriminant, so that:

```
hash = SHA-256( borsh( NEP_discriminant || chain_id || DelegateAction ) )
```

This is a protocol-breaking change requiring a new `ProtocolFeature` gate and a new `DelegateAction` version (or a new `SignableMessageType` variant). [8](#0-7) 

---

### Proof of Concept

1. Alice holds account `alice.near` on both mainnet and testnet with the same ED25519 key pair. Mainnet access-key nonce = 50; testnet access-key nonce = 10.

2. Alice signs a `DelegateAction` on mainnet (nonce = 51, max_block_height = 200,000,000, actions = [Transfer 10 NEAR to bob.near]) and hands it to a relayer.

3. The relayer, instead of (or in addition to) submitting on mainnet, wraps the identical `SignedDelegateAction` in a testnet `SignedTransaction` and broadcasts it on testnet.

4. `apply_delegate_action` on testnet:
   - `signed_delegate_action.verify()` → **passes** (same key, same hash, no chain binding).
   - `block_height (e.g. 150,000,000) > max_block_height (200,000,000)` → **passes** (not expired).
   - `sender_id` match → **passes**.
   - Nonce 51 > current testnet nonce 10 → **passes**.

5. A receipt is created transferring 10 testnet NEAR from `alice.near` to `bob.near` — an action Alice never authorized on testnet. [9](#0-8) [10](#0-9)

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

**File:** core/primitives/src/action/delegate.rs (L83-95)
```rust
impl SignedDelegateAction {
    pub fn verify(&self) -> bool {
        let delegate_action = &self.delegate_action;
        let hash = delegate_action.get_nep461_hash();
        let public_key = &delegate_action.public_key;

        self.signature.verify(hash.as_ref(), public_key)
    }

    pub fn sign(singer: &Signer, delegate_action: DelegateAction) -> Self {
        let signature = singer.sign(delegate_action.get_nep461_hash().as_bytes());
        Self { delegate_action, signature }
    }
```

**File:** core/primitives/src/action/delegate.rs (L176-184)
```rust
    /// Delegate action hash used for NEP-461 signature scheme which tags
    /// different messages before hashing
    ///
    /// For more details, see: [NEP-461](https://github.com/near/NEPs/pull/461)
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateActionV2);
        let bytes = borsh::to_vec(&signable).expect("failed to serialize");
        hash(&bytes)
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

**File:** core/primitives/src/signable_message.rs (L61-107)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
#[non_exhaustive]
pub enum SignableMessageType {
    /// A delegate action, intended for a relayer to included it in an action list of a transaction.
    DelegateAction,
    /// A delegate action with gas key support, intended for a relayer to include it in an action
    /// list of a transaction.
    DelegateActionV2,
}

#[derive(thiserror::Error, Debug)]
#[non_exhaustive]
pub enum ReadDiscriminantError {
    #[error("does not fit any known categories")]
    UnknownMessageType,
    #[error("NEP {0} does not have a known on-chain use")]
    UnknownOnChainNep(u32),
    #[error("NEP {0} does not have a known off-chain use")]
    UnknownOffChainNep(u32),
    #[error("discriminant is in the range for transactions")]
    TransactionFound,
}

#[derive(thiserror::Error, Debug)]
#[non_exhaustive]
pub enum CreateDiscriminantError {
    #[error("nep number {0} is too big")]
    NepTooLarge(u32),
}

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
```

**File:** core/primitives/src/signable_message.rs (L217-228)
```rust
impl From<SignableMessageType> for MessageDiscriminant {
    fn from(ty: SignableMessageType) -> Self {
        // unwrapping here is ok, we know the constant NEP numbers used are in range
        match ty {
            SignableMessageType::DelegateAction => {
                MessageDiscriminant::new_on_chain(NEP_366_META_TRANSACTIONS).unwrap()
            }
            SignableMessageType::DelegateActionV2 => {
                MessageDiscriminant::new_on_chain(NEP_611_GAS_KEYS).unwrap()
            }
        }
    }
```

**File:** runtime/runtime/src/actions.rs (L422-453)
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
        return Ok(());
    }
```

**File:** core/primitives/src/transaction.rs (L139-144)
```rust
impl Transaction {
    /// Computes a hash of the transaction for signing and size of serialized transaction
    pub fn get_hash_and_size(&self) -> (CryptoHash, u64) {
        let bytes = borsh::to_vec(&self).expect("Failed to deserialize");
        (hash(&bytes), bytes.len() as u64)
    }
```
