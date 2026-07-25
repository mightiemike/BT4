### Title
`DelegateAction` Signed Payload Contains No Chain-Specific Binding, Enabling Cross-Chain Replay of Meta-Transactions — (`File: core/primitives/src/action/delegate.rs`)

---

### Summary

`DelegateAction` (NEP-366 meta-transactions) are signed off-chain by a user and forwarded by a relayer. The signed payload hashed by `get_nep461_hash()` contains only a NEP-number discriminant and the action fields (`sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, `public_key`). No chain-specific identifier — genesis hash, chain ID, or network name — is included. A valid `SignedDelegateAction` produced for one NEAR network (e.g., testnet) can be replayed by a malicious relayer on another NEAR network (e.g., mainnet) where the same account and key exist with a compatible nonce, causing unauthorized execution of the inner actions on the victim's mainnet account.

---

### Finding Description

**Regular `SignedTransaction`** includes a `block_hash` field that anchors it to a specific chain: [1](#0-0) 

The `block_hash` must be a recent block hash on the current chain, enforced by `check_transaction_validity_period`. This makes regular transactions chain-specific.

**`DelegateAction`** has no equivalent field: [2](#0-1) 

The signed hash is computed as: [3](#0-2) 

`SignableMessage::new` wraps the action with only a constant `MessageDiscriminant` derived from the NEP number (366 or 611): [4](#0-3) 

The discriminant is a fixed `u32` constant — not chain-specific: [5](#0-4) 

So the full signed payload is: `SHA256(borsh(u32(0x4000016E) || DelegateAction))` — identical on every NEAR-based network.

At execution time, `apply_delegate_action` verifies the signature, checks `max_block_height`, validates the nonce, and checks the sender's access key — but never checks that the action was intended for the current chain: [6](#0-5) 

---

### Impact Explanation

If a user signs a `DelegateAction` for a testnet relayer (e.g., to call `ft_transfer` on testnet), a malicious relayer can submit the same `SignedDelegateAction` on mainnet, provided:

1. The user's `sender_id` account exists on mainnet with the same `public_key` registered (common — many users share keys across networks).
2. The mainnet access key nonce is lower than the `DelegateAction`'s `nonce` (plausible, especially for newer accounts or keys).
3. The mainnet block height is below `max_block_height` (plausible when `max_block_height` is set generously, e.g., `current_height + 100` on testnet, which may still be in the future on mainnet if testnet height > mainnet height, or vice versa with a large window).

The inner `actions` execute on mainnet with `predecessor_id = sender_id` (the victim), meaning:
- Token transfers drain the victim's mainnet balance.
- `FunctionCall` actions execute as the victim on mainnet contracts (e.g., `ft_transfer` drains fungible token balances).
- `AddKey`/`DeleteKey` actions modify the victim's mainnet access keys.

This constitutes **unauthorized transaction execution** and **stealing or loss of funds** from an unprivileged attacker position (a relayer the user voluntarily contacted, or anyone who intercepts the off-chain `SignedDelegateAction`).

---

### Likelihood Explanation

- NEAR users routinely use the same account ID and key pair on both mainnet and testnet.
- Relayers are third-party services; a malicious relayer is a realistic threat model for meta-transactions.
- The `SignedDelegateAction` is transmitted off-chain (e.g., via HTTP to a relayer API), where it can be intercepted or logged.
- The `max_block_height` window is application-controlled and often set to hundreds of blocks for usability, leaving a replay window.
- The nonce condition is satisfiable whenever the victim's mainnet key nonce is lower than the testnet-signed nonce (e.g., the victim is more active on testnet than mainnet).

---

### Recommendation

Include a chain-specific binding in the `DelegateAction` signed payload. The standard approach is to add a `chain_id` or genesis block hash field to `DelegateAction` (and `DelegateActionV2`) that is committed to in the signature and validated in `apply_delegate_action` against the current chain's genesis hash (available via `ApplyState`). Alternatively, the `SignableMessage` discriminant scheme could be extended to incorporate a per-network constant, analogous to EIP-712's `domainSeparator`. [2](#0-1) [7](#0-6) [6](#0-5) 

---

### Proof of Concept

1. Alice has account `alice.near` on both mainnet and testnet, with the same ED25519 key pair. Mainnet access key nonce = 5, testnet access key nonce = 10.

2. Alice signs a `DelegateAction` for a testnet relayer:
   ```
   DelegateAction {
     sender_id: "alice.near",
     receiver_id: "token.testnet",
     actions: [FunctionCall { method_name: "ft_transfer", args: {receiver_id: "attacker.testnet", amount: "1000"}, deposit: 1 }],
     nonce: 11,
     max_block_height: testnet_height + 200,
     public_key: alice_pubkey,
   }
   ```
   Signed hash = `SHA256(borsh(0x4000016E || DelegateAction))` — identical on mainnet.

3. The malicious relayer constructs a mainnet outer transaction:
   ```
   SignedTransaction {
     signer_id: "relayer.near",
     receiver_id: "alice.near",   // routes DelegateAction to alice's shard
     actions: [Delegate(SignedDelegateAction { delegate_action: <above>, signature: <alice's sig> })],
     block_hash: <recent mainnet block hash>,
   }
   ```

4. `apply_delegate_action` on mainnet:
   - `signed_delegate_action.verify()` → **passes** (same hash, same key, same signature).
   - `block_height <= max_block_height` → **passes** (if mainnet height < testnet_height + 200).
   - `sender_id == "alice.near"` → **passes**.
   - `validate_delegate_action_key`: nonce 11 > mainnet ak_nonce 5 → **passes**.
   - A new receipt is created: `predecessor_id = "alice.near"`, `receiver_id = "token.testnet"` (or the mainnet equivalent), executing `ft_transfer` as Alice on mainnet.

5. Alice's mainnet fungible tokens are transferred to the attacker without Alice's mainnet authorization. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** core/primitives/src/transaction.rs (L118-136)
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

**File:** core/primitives/src/action/delegate.rs (L83-90)
```rust
impl SignedDelegateAction {
    pub fn verify(&self) -> bool {
        let delegate_action = &self.delegate_action;
        let hash = delegate_action.get_nep461_hash();
        let public_key = &delegate_action.public_key;

        self.signature.verify(hash.as_ref(), public_key)
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

**File:** core/primitives/src/signable_message.rs (L61-65)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}
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

**File:** core/primitives/src/signable_message.rs (L217-229)
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
