### Title
`DelegateAction` Signing Hash Omits `chain_id`, Enabling Cross-Chain Replay of Meta-Transaction Signatures — (`core/primitives/src/action/delegate.rs`)

---

### Summary

`DelegateAction` (NEP-366 meta-transaction) signatures are not bound to any specific NEAR network. The signing payload contains no `chain_id` or chain-specific anchor. A signed `DelegateAction` captured on one NEAR network (e.g., testnet) can be replayed verbatim on another (e.g., mainnet) by any relayer, provided the same account and key exist there with a nonce that satisfies the replay condition. This is the direct nearcore analog of the PayrollManager bug: a missing binding context in the signature domain allows cross-context reuse of valid signatures.

---

### Finding Description

`DelegateAction::get_nep461_hash()` constructs the signed digest as:

```
SHA-256( borsh( MessageDiscriminant(NEP=366) || DelegateAction ) )
``` [1](#0-0) 

`DelegateAction` contains: `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, `public_key`. [2](#0-1) 

`SignableMessage` wraps only a `MessageDiscriminant` (a NEP-number-derived `u32`, identical on every NEAR network) and the message body. There is no `chain_id` field anywhere in the signing domain. [3](#0-2) 

By contrast, a regular `SignedTransaction` includes `block_hash` in its signed body — a value that is chain-specific because every chain produces distinct block hashes. `DelegateAction` replaces `block_hash` with `max_block_height`, a plain integer that is identical across all NEAR networks at the same height. [4](#0-3) 

`apply_delegate_action` performs four checks before executing the inner actions: signature validity, block-height expiry, `sender_id` match, and nonce validity. None of these checks are chain-specific. [5](#0-4) 

---

### Impact Explanation

An attacker who obtains a `SignedDelegateAction` produced for testnet (or any other NEAR network) can submit it on mainnet via a relayer transaction. If the victim's mainnet access key nonce is strictly less than the nonce embedded in the captured `DelegateAction`, the runtime will accept it as valid and execute the inner actions with `sender_id` (the victim) as `predecessor_id`. Inner actions can include `Transfer`, `FunctionCall` with attached deposit, `AddKey`, `DeleteKey`, or `DeleteAccount` — all of which can drain or permanently compromise the victim's mainnet account.

---

### Likelihood Explanation

The preconditions are:

1. The victim uses the same ED25519 key on both mainnet and testnet — extremely common among developers and power users who generate one key pair and add it to accounts on multiple networks.
2. The victim's mainnet key nonce is lower than the nonce in the captured `DelegateAction`. Because testnet accounts are often used for experimentation before mainnet, testnet nonces frequently advance ahead of mainnet nonces for the same key.
3. The attacker operates or controls a relayer, or submits the outer transaction directly. Relayer infrastructure is public and permissionless.

A developer who tests a meta-transaction flow on testnet and then deploys the same key to mainnet satisfies all three conditions simultaneously.

---

### Recommendation

Include `chain_id` in the `DelegateAction` signing payload, mirroring EIP-712's `chainId` domain separator. The simplest approach is to add `chain_id: String` to `DelegateAction` and `DelegateActionV2`, or to incorporate it into `SignableMessage` as a domain-separation field alongside the NEP discriminant:

```rust
// In SignableMessage or DelegateAction::get_nep461_hash():
let signable = SignableMessageWithChain {
    discriminant: MessageDiscriminant::new_on_chain(NEP_366_META_TRANSACTIONS).unwrap(),
    chain_id: &chain_id,   // e.g. "mainnet", "testnet"
    msg: &self,
};
```

Alternatively, replace `max_block_height` with a `block_hash` (as regular transactions use), which is inherently chain-specific. Either change requires a protocol version gate and a new `DelegateAction` version, since it alters the signing domain.

---

### Proof of Concept

1. Alice holds key `K` on both `mainnet` and `testnet`, with mainnet nonce = 0 and testnet nonce = 0.
2. Alice signs a `DelegateAction` on testnet: `sender_id="alice.near"`, `receiver_id="token.testnet"`, `actions=[Transfer{deposit=1_000_000}]`, `nonce=1`, `max_block_height=99999999`, `public_key=K`.
3. Attacker captures the `SignedDelegateAction` (e.g., from a public relayer API or mempool).
4. Attacker wraps it in a mainnet `SignedTransaction`: `signer=attacker.near`, `receiver=alice.near`, `actions=[DelegateAction(captured)]`.
5. Mainnet runtime calls `apply_delegate_action`:
   - `signed_delegate_action.verify()` → **passes** (signature is over chain-agnostic bytes)
   - `block_height > max_block_height` → **passes** (height 99999999 is far in the future)
   - `sender_id == "alice.near"` → **passes**
   - nonce 1 > current mainnet nonce 0 → **passes**
6. A new receipt is emitted with `predecessor_id="alice.near"`, executing the transfer on mainnet and draining Alice's mainnet balance. [6](#0-5) [7](#0-6)

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

**File:** core/primitives/src/signable_message.rs (L61-65)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}
```

**File:** core/primitives/src/transaction.rs (L139-145)
```rust
impl Transaction {
    /// Computes a hash of the transaction for signing and size of serialized transaction
    pub fn get_hash_and_size(&self) -> (CryptoHash, u64) {
        let bytes = borsh::to_vec(&self).expect("Failed to deserialize");
        (hash(&bytes), bytes.len() as u64)
    }
}
```

**File:** runtime/runtime/src/actions.rs (L430-469)
```rust
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

    // Generate a new receipt from DelegateAction.
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });
```
