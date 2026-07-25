I have enough information to produce the final analysis. Let me verify one last detail about the DelegateAction signing path.

### Title
`DelegateAction` Signed Payload Omits Chain Identifier, Enabling Cross-Network Replay by a Malicious Relayer — (`File: core/primitives/src/action/delegate.rs`, `core/primitives/src/signable_message.rs`)

---

### Summary

`DelegateAction` (NEP-366 meta transactions) and `DelegateActionV2` (NEP-611) sign a payload that contains no chain-specific identifier — no `chain_id`, no `network_id`, and no `block_hash`. A malicious relayer who receives a user-signed `DelegateAction` intended for one NEAR network (e.g., testnet) can submit it verbatim on a different NEAR network (e.g., mainnet), executing the inner actions against the user's mainnet account without the user's consent.

---

### Finding Description

**Regular transactions are protected.** `TransactionV0` and `TransactionV1` both carry a `block_hash` field that is verified by `check_transaction_validity_period` to be an ancestor of the current chain's canonical head. Because mainnet and testnet have disjoint block hashes, a transaction signed for one network is cryptographically rejected on the other. [1](#0-0) [2](#0-1) 

**`DelegateAction` is not protected.** The struct carries `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key` — but no `block_hash` and no `chain_id`: [3](#0-2) 

The signing hash is produced by `get_nep461_hash()`, which wraps the action in a `SignableMessage` containing only a 4-byte NEP discriminant and the serialised action body: [4](#0-3) [5](#0-4) 

The `SignableMessage` struct has no chain-specific field: [6](#0-5) 

The same absence applies to `DelegateActionV2` / `VersionedDelegateActionPayload`: [7](#0-6) [8](#0-7) 

The `max_block_height` field provides a time window, but block heights are not chain-specific: mainnet and testnet both count blocks from their respective genesis, and their height ranges overlap. A `DelegateAction` signed with `max_block_height = N` is valid on any NEAR network whose current height is below `N`.

---

### Impact Explanation

A malicious relayer who obtains a user-signed `DelegateAction` (e.g., a testnet meta-transaction) can submit it on mainnet. If the user holds the same account ID and the same key pair on mainnet, and the nonce is valid there, the runtime will:

1. Verify the signature — it passes, because the signed bytes are identical across networks.
2. Verify the nonce — it passes if the mainnet access-key nonce is lower than the delegate nonce.
3. Execute the inner `actions` — transfers, function calls, key additions/deletions — against the user's **mainnet** account.

Concrete impacts within the allowed scope:
- **Stealing or loss of funds**: a `TransferAction` inside the delegate drains mainnet balance.
- **Unauthorized transaction**: the user never authorized execution on mainnet.
- **Contract execution flow breakage**: arbitrary function calls execute on mainnet contracts.

---

### Likelihood Explanation

Prerequisites that must hold simultaneously:

1. The user has the same account ID on both networks (common for developers and power users).
2. The user has added the same key pair to both accounts (common when reusing a hardware wallet or a seed phrase).
3. The mainnet access-key nonce is lower than the delegate nonce (likely if the key is newer on mainnet than on testnet).
4. The current mainnet block height is below `max_block_height` (depends on the value chosen by the user's wallet).
5. The relayer is malicious or compromised.

Conditions 1–4 are realistic for a non-trivial fraction of NEAR users. Condition 5 is the primary gating factor; however, the meta-transaction model explicitly does not require the user to trust the relayer for correctness of execution — only for liveness. The absence of a chain binding breaks that assumption.

---

### Recommendation

Include the network's `chain_id` (the genesis `chain_id` string, e.g., `"mainnet"` or `"testnet"`) in the signed payload of `DelegateAction` and `DelegateActionV2`. The simplest approach is to add a `chain_id: String` field to both structs and include it in the Borsh-serialised body that feeds `get_nep461_hash()`. Alternatively, encode the `chain_id` inside the `SignableMessage` wrapper so that all future message types inherit the binding automatically.

Because `DelegateAction` is a protocol type, this requires a protocol-version gate (a new `ProtocolFeature`) and a migration path: old signed delegate actions (without `chain_id`) must be rejected once the feature activates, or the new field must be made mandatory at the serialisation level.

---

### Proof of Concept

```
Setup:
  - Account "alice.near" exists on both mainnet and testnet.
  - Alice's ed25519 key pair K is registered on both accounts.
  - Mainnet access-key nonce for K = 0.
  - Testnet access-key nonce for K = 5.

Step 1 (testnet):
  Alice signs a DelegateAction:
    sender_id    = "alice.near"
    receiver_id  = "bob.near"
    actions      = [Transfer { deposit: 10 NEAR }]
    nonce        = 6          // valid on testnet (> 5)
    max_block_height = 200_000_000  // far future on both networks
    public_key   = K.public

  Signed hash = SHA-256( borsh(discriminant=0x4000016E) || borsh(DelegateAction) )
  // No chain identifier in the preimage.

Step 2 (malicious relayer):
  Relayer wraps the signed DelegateAction in a mainnet transaction
  and submits it to a mainnet RPC node.

Step 3 (mainnet runtime):
  - signature.verify(hash, K.public) → true  (same bytes, same key)
  - nonce 6 > mainnet ak_nonce 0             → valid
  - block_height < max_block_height          → valid
  - Transfer executes: 10 NEAR leaves alice.near on mainnet.

Result: Alice loses 10 mainnet NEAR without ever authorising a mainnet transaction.
``` [9](#0-8) [10](#0-9)

### Citations

**File:** core/primitives/src/transaction.rs (L33-48)
```rust
pub struct TransactionV0 {
    /// An account on which behalf transaction is signed
    pub signer_id: AccountId,
    /// A public key of the access key which was used to sign an account.
    /// Access key holds permissions for calling certain kinds of actions.
    pub public_key: PublicKey,
    /// Nonce is used to determine order of transaction in the pool.
    /// It increments for a combination of `signer_id` and `public_key`
    pub nonce: Nonce,
    /// Receiver account for this transaction
    pub receiver_id: AccountId,
    /// The hash of the block in the blockchain on top of which the given transaction is valid
    pub block_hash: CryptoHash,
    /// A list of actions to be applied
    pub actions: Vec<Action>,
}
```

**File:** chain/chain/src/store/utils.rs (L56-75)
```rust
pub fn check_transaction_validity_period(
    chain_store: &ChainStoreAdapter,
    prev_block_header: &BlockHeader,
    base_block_hash: &CryptoHash,
    transaction_validity_period: BlockHeightDelta,
) -> Result<(), InvalidTxError> {
    let base_header =
        chain_store.get_block_header(base_block_hash).map_err(|_| InvalidTxError::Expired)?;

    metrics::CHAIN_VALIDITY_PERIOD_CHECK_DELAY
        .observe(prev_block_header.height().saturating_sub(base_header.height()) as f64);

    // First check the distance between blocks
    if prev_block_header.height() > base_header.height() + transaction_validity_period {
        return Err(InvalidTxError::Expired);
    }

    // Then check if there is a path between the blocks (`base` is an ancestor of `prev`)
    validity_period_validate_is_ancestor(&base_header, prev_block_header, chain_store)
}
```

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

**File:** core/primitives/src/action/delegate.rs (L119-133)
```rust
pub struct DelegateActionV2 {
    /// Signer of the delegated actions
    pub sender_id: AccountId,
    /// Receiver of the delegated actions.
    pub receiver_id: AccountId,
    /// List of actions to be executed.
    pub actions: Vec<NonDelegateAction>,
    /// Nonce of the signing key, advanced when this action is processed. For
    /// a gas key it also selects which of the parallel nonces to advance.
    pub nonce: TransactionNonce,
    /// The maximal height of the block in the blockchain below which the given DelegateActionV2 is valid.
    pub max_block_height: BlockHeight,
    /// Public key used to sign this delegated action.
    pub public_key: PublicKey,
}
```

**File:** core/primitives/src/action/delegate.rs (L180-184)
```rust
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateActionV2);
        let bytes = borsh::to_vec(&signable).expect("failed to serialize");
        hash(&bytes)
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

**File:** core/primitives/src/signable_message.rs (L61-108)
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
}
```
