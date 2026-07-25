Based on my thorough analysis of the codebase, I have found a valid analog.

---

### Title
`DelegateAction` Signed Payload Lacks Chain Identifier, Enabling Cross-Chain Replay of Meta-Transactions — (`core/primitives/src/action/delegate.rs`, `core/primitives/src/signable_message.rs`)

### Summary

`DelegateAction` (NEP-366 meta-transactions) and `DelegateActionV2` (NEP-611 gas-key variant) produce their signing hash from a payload that contains no chain identifier. A `SignedDelegateAction` created and intercepted on one NEAR network (e.g., testnet) can be submitted verbatim on any other NEAR network (e.g., mainnet) by a malicious relayer or an observer, causing the inner actions to execute on the victim's account on the unintended chain. Regular `SignedTransaction` is immune because it commits to a `block_hash` that is chain-specific; `DelegateAction` has no equivalent binding.

### Finding Description

`DelegateAction` is defined in `core/primitives/src/action/delegate.rs`:

```rust
pub struct DelegateAction {
    pub sender_id: AccountId,
    pub receiver_id: AccountId,
    pub actions: Vec<NonDelegateAction>,
    pub nonce: Nonce,
    pub max_block_height: BlockHeight,
    pub public_key: PublicKey,
}
``` [1](#0-0) 

Its signing hash is computed in `get_nep461_hash()`:

```rust
pub fn get_nep461_hash(&self) -> CryptoHash {
    let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
    let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
    hash(&bytes)
}
``` [2](#0-1) 

`SignableMessage` prepends only a `MessageDiscriminant` — a fixed u32 constant (`2^30 + 366` for V1, `2^30 + 611` for V2) — before the serialized action body:

```rust
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}
``` [3](#0-2) 

The discriminant is derived purely from the NEP number and is **identical on every NEAR network** (mainnet, testnet, localnet, any fork):

```rust
SignableMessageType::DelegateAction =>
    MessageDiscriminant::new_on_chain(NEP_366_META_TRANSACTIONS).unwrap()
``` [4](#0-3) 

The same absence applies to `DelegateActionV2` / `VersionedDelegateActionPayload::get_nep461_hash()`: [5](#0-4) 

By contrast, a regular `SignedTransaction` commits to `block_hash` — a hash of a specific block on a specific chain — making cross-chain replay structurally impossible:

```rust
pub struct TransactionV0 {
    ...
    pub block_hash: CryptoHash,
    ...
}
``` [6](#0-5) 

The runtime's `apply_delegate_action` verifies the signature and nonce but has no chain-binding check: [7](#0-6) 

`validate_delegate_action_key` enforces nonce monotonicity and `max_block_height` but neither field is chain-specific: [8](#0-7) 

### Impact Explanation

An unprivileged attacker (a malicious relayer, or any observer of on-chain or off-chain `SignedDelegateAction` data) can replay a victim's signed meta-transaction on a different NEAR network. The inner actions execute with `sender_id` as the predecessor, so the victim's account on the target chain performs the actions: token transfers, key additions/deletions, function calls with deposits, contract deployments, or account deletion. This constitutes **unauthorized transaction execution** and **fund theft** from the victim's mainnet account using a signature the victim only intended for testnet (or vice versa).

### Likelihood Explanation

The preconditions are realistic:

1. **Same account on multiple chains**: NEAR account names are human-readable and the same name (e.g., `alice.near`) commonly exists on both mainnet and testnet.
2. **Same key pair**: Users and wallets routinely reuse key pairs across networks.
3. **Valid nonce on target chain**: The nonce in `DelegateAction` must exceed the current nonce on the target chain. Because nonces are monotonically increasing and independent per chain, a testnet nonce is frequently valid on mainnet (e.g., testnet nonce 50 is valid on mainnet if the mainnet key nonce is < 50).
4. **`max_block_height` not expired**: Block heights on mainnet and testnet are independent; a `max_block_height` set for testnet is almost always valid on mainnet.
5. **Attacker obtains the signed payload**: `SignedDelegateAction` is transmitted off-chain to relayers and on-chain inside transactions — both are observable.

### Recommendation

Add a `chain_id: String` field to both `DelegateAction` and `DelegateActionV2`. Include it in the Borsh-serialized payload that feeds `get_nep461_hash()`. The runtime's `apply_delegate_action` must then verify that `delegate_action.chain_id == apply_state.chain_id` before accepting the signature. This mirrors how `block_hash` binds a regular `SignedTransaction` to a specific chain.

Alternatively, incorporate the chain ID into the `MessageDiscriminant` or into a wrapper struct passed to `SignableMessage::new`, so that the signed bytes are chain-specific without a protocol-breaking struct change.

### Proof of Concept

**Setup:**
- Alice has account `alice.near` on both testnet and mainnet, with the same ed25519 key pair, mainnet key nonce = 3, testnet key nonce = 3.

**Step 1 — Alice signs a testnet meta-transaction:**
```rust
let delegate_action = DelegateAction {
    sender_id: "alice.near".parse().unwrap(),
    receiver_id: "bob.near".parse().unwrap(),
    actions: vec![transfer_100_near],
    nonce: 4,                        // valid on testnet (> 3)
    max_block_height: 200_000_000,   // far future, valid on both chains
    public_key: alice_key,
};
// hash = SHA256(discriminant_366 || borsh(delegate_action))
// NO chain_id in the hash
let signature = alice_sk.sign(delegate_action.get_nep461_hash().as_bytes());
let signed = SignedDelegateAction { delegate_action, signature };
```

**Step 2 — Attacker intercepts `signed` (e.g., from testnet mempool or relayer API).**

**Step 3 — Attacker submits to mainnet:**
```rust
// Outer transaction signed by attacker's mainnet key
let mainnet_tx = SignedTransaction::from_actions(
    attacker_nonce,
    attacker_account,
    "alice.near".parse().unwrap(),  // outer receiver = delegate sender
    &attacker_signer,
    vec![Action::Delegate(Box::new(signed))],  // same signed payload
    mainnet_block_hash,
);
// Submit via mainnet RPC
```

**Step 4 — Runtime on mainnet:**
- `apply_delegate_action` calls `signed_delegate_action.verify()` → **passes** (same hash, same signature, same key)
- Nonce check: mainnet nonce 4 > stored nonce 3 → **passes**
- `max_block_height` check → **passes**
- A new receipt is created: `predecessor = alice.near`, `receiver = bob.near`, action = Transfer 100 NEAR
- Alice loses 100 NEAR on mainnet without ever authorizing a mainnet transaction. [9](#0-8) [10](#0-9)

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

**File:** core/primitives/src/signable_message.rs (L61-65)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
}
```

**File:** core/primitives/src/signable_message.rs (L221-223)
```rust
            SignableMessageType::DelegateAction => {
                MessageDiscriminant::new_on_chain(NEP_366_META_TRANSACTIONS).unwrap()
            }
```

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
