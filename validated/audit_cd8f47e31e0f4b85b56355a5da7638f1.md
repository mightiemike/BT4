Now I have enough information to make a complete assessment. Let me verify the exact signing path for both `DelegateAction` versions and confirm no chain ID is present anywhere in the hash.

### Title
Missing Chain ID in `DelegateAction` Signing Hash Enables Cross-Network Signature Replay — (File: `core/primitives/src/action/delegate.rs`)

---

### Summary

The `DelegateAction` (NEP-366) and `DelegateActionV2` (NEP-611) off-chain signing hashes do not include any network-identifying value (chain ID or genesis hash). A `SignedDelegateAction` produced on one NEAR network is cryptographically valid on any other NEAR network where the same `sender_id` account holds the same `public_key` with a matching nonce. An unprivileged attacker who observes a `SignedDelegateAction` on one network can re-wrap it in a fresh outer transaction and submit it to a different network, executing the inner actions without the sender's authorization on that network.

---

### Finding Description

`DelegateAction.get_nep461_hash()` computes the signing payload as:

```
hash( borsh( SignableMessage { discriminant: (MIN_ON_CHAIN_DISCRIMINANT + 366), msg: DelegateAction } ) )
``` [1](#0-0) 

`SignableMessage` contains only a fixed u32 discriminant (the NEP number) and the `DelegateAction` body: [2](#0-1) 

The discriminant is a compile-time constant derived solely from the NEP number — it carries no network identity: [3](#0-2) 

The `DelegateAction` struct itself contains `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key` — **no chain ID, no genesis hash**: [4](#0-3) 

`grep` confirms zero occurrences of `chain_id` in both `delegate.rs` and `signable_message.rs`.

The same omission applies to `DelegateActionV2` / `VersionedDelegateActionPayload.get_nep461_hash()`, which uses `SignableMessageType::DelegateActionV2` (NEP-611 discriminant) — also a fixed constant with no network binding: [5](#0-4) 

The outer `SignedTransaction` does include a `block_hash` that binds it to a specific chain (via `transaction_validity_period`). However, the inner `SignedDelegateAction` is an independent signed object. An attacker can extract it from a transaction on network A and re-wrap it in a brand-new outer `SignedTransaction` with a valid `block_hash` from network B. The runtime's `apply_delegate_action` path verifies only the inner signature, nonce, and access key — none of which are network-scoped: [6](#0-5) 

---

### Impact Explanation

If the same `sender_id` account exists on two NEAR networks with the same `public_key` registered and a nonce that satisfies `delegate_nonce > access_key.nonce` on the target network, the attacker can replay the `SignedDelegateAction` on the target network. The inner actions execute with `sender_id` as the predecessor — meaning token transfers, function calls, key additions/deletions, or any other `NonDelegateAction` execute as if authorized by the sender on the target network. This constitutes an **unauthorized transaction** and potential **loss of funds**.

---

### Likelihood Explanation

Practical exploitability is constrained by two factors:

1. **Account namespace separation**: NEAR mainnet top-level accounts end in `.near`; testnet accounts end in `.testnet`. A `DelegateAction` signed by `alice.testnet` cannot be replayed on mainnet because `alice.testnet` does not exist there.
2. **Nonce alignment**: The nonce in the `DelegateAction` must be strictly greater than the current access key nonce on the target network. For a fresh account on the target network (nonce = 0), any nonce ≥ 1 satisfies this.

The realistic attack surface is: (a) private or permissioned NEAR networks that share account IDs with mainnet/testnet, (b) betanet or other official NEAR networks where the same account ID and key pair can be registered, and (c) future network configurations. The `max_block_height` field provides time-bounding but does not prevent cross-network replay within the valid height window, since block heights on different networks are independent counters.

---

### Recommendation

Include the chain ID (or genesis block hash) in the `DelegateAction` signing payload, analogous to EIP-712's `verifyingContract`/`chainId` domain separator. Concretely, add a `chain_id: String` field to `DelegateAction` and `DelegateActionV2`, or incorporate it into the `SignableMessage` discriminant / a domain-separator prefix. The runtime's `apply_delegate_action` must then verify that the chain ID in the signed payload matches the executing network's chain ID (available via `ApplyState`). [4](#0-3) [7](#0-6) 

---

### Proof of Concept

**Setup**: Two NEAR networks — `mainnet` and `private-net` — both running nearcore. Account `alice.near` exists on both with the same ED25519 key pair. On `private-net`, `alice.near`'s access key nonce is 0.

**Step 1 — Sign on private-net**: Alice (or an attacker controlling `private-net`) creates and signs a `DelegateAction` on `private-net`:

```rust
let delegate_action = DelegateAction {
    sender_id: "alice.near".parse().unwrap(),
    receiver_id: "bob.near".parse().unwrap(),
    actions: vec![TransferAction { deposit: 100_000_000_000_000_000_000_000_000 }.into()], // 100 NEAR
    nonce: 1,          // valid: > private-net access key nonce (0)
    max_block_height: 999_999_999,
    public_key: alice_key.public_key(),
};
let signed = SignedDelegateAction::sign(&alice_key, delegate_action);
// signed.verify() == true on ANY network — no chain ID in hash
```

**Step 2 — Replay on mainnet**: The attacker wraps the identical `signed` in a new outer transaction using a valid mainnet `block_hash`:

```rust
let mainnet_tx = SignedTransaction::from_actions(
    relayer_nonce,
    relayer_id.clone(),
    "alice.near".parse().unwrap(),  // outer receiver = delegate sender
    &relayer_key,
    vec![Action::Delegate(Box::new(signed))],  // same SignedDelegateAction
    mainnet_block_hash,             // fresh valid mainnet block hash
);
```

**Step 3 — Execution**: The mainnet runtime receives the transaction. `apply_delegate_action` calls `signed_delegate_action.verify()`, which recomputes `hash(borsh(SignableMessage { discriminant: NEP_366_const, msg: delegate_action }))` — identical on both networks. Signature check passes. Nonce check passes (mainnet `alice.near` nonce = 0 < 1). The transfer of 100 NEAR executes on mainnet without Alice's authorization. [8](#0-7) [9](#0-8)

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

**File:** core/primitives/src/action/delegate.rs (L83-96)
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

**File:** runtime/runtime/src/actions.rs (L535-556)
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
```

**File:** runtime/runtime/src/actions.rs (L604-622)
```rust
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
