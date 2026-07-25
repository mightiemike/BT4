### Title
`DelegateAction` Signed Payload Lacks Chain-ID Domain Separation, Enabling Cross-Chain Replay of Meta-Transactions — (`core/primitives/src/action/delegate.rs`)

---

### Summary

`DelegateAction` (NEP-366 meta-transactions) and `DelegateActionV2` (NEP-611 gas-key variant) sign a payload that contains no chain identifier. A `SignedDelegateAction` produced on one NEAR network (e.g., testnet) is cryptographically valid on any other NEAR network (e.g., mainnet) where the same key exists with a compatible nonce and a non-expired `max_block_height`. An unprivileged attacker who intercepts or observes a signed delegate action can replay it on a different network, causing the sender's account on that network to execute actions the sender never authorized there.

---

### Finding Description

`DelegateAction.get_nep461_hash()` constructs the signed preimage as:

```
SHA-256( borsh( MessageDiscriminant(NEP-366) || DelegateAction ) )
``` [1](#0-0) 

`VersionedDelegateActionPayload.get_nep461_hash()` (used by `DelegateActionV2`) does the same with `SignableMessageType::DelegateActionV2`: [2](#0-1) 

The `SignableMessage` wrapper that produces the preimage contains only a `MessageDiscriminant` (a 4-byte NEP number) and the serialized action body — no chain identifier of any kind: [3](#0-2) 

The `MessageDiscriminant` is a plain `u32` derived from the NEP number. It separates message types (delegate vs. transaction) but carries no network identity: [4](#0-3) 

The `DelegateAction` struct itself contains `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key` — but no `block_hash` and no chain/genesis identifier: [5](#0-4) 

Contrast this with a regular `SignedTransaction`, whose signed preimage includes `block_hash` — a chain-specific value that implicitly binds the signature to the chain where that block exists: [6](#0-5) 

`DelegateAction` deliberately omits `block_hash` (it uses `max_block_height` instead, which is a plain integer shared across all NEAR networks). The runtime's `apply_delegate_action` verifies the signature and checks `max_block_height` and nonce, but performs no chain-binding check: [7](#0-6) 

---

### Impact Explanation

An attacker who obtains a `SignedDelegateAction` (e.g., by observing a testnet transaction, intercepting a relayer's off-chain message, or monitoring a public mempool) can submit it verbatim to a different NEAR network. The runtime will accept it if:

1. The `sender_id` account exists on the target chain with the same `public_key`.
2. The access-key nonce on the target chain is less than the `nonce` in the delegate action.
3. The current block height on the target chain is less than `max_block_height`.

All three conditions are routinely satisfied: many users derive the same key for testnet and mainnet; nonces on a fresh mainnet account start at 0; and `max_block_height` values are typically set hundreds of blocks in the future.

The executed inner actions can include `Transfer`, `FunctionCall`, `AddKey`, `DeleteKey`, or `DeleteAccount` — all of which directly affect the sender's funds or account control on the target chain without the sender's knowledge or consent.

---

### Likelihood Explanation

- **Same key on multiple networks**: NEAR accounts are created with user-chosen keys; it is standard practice (and the default in most tooling) to reuse the same ed25519 keypair on testnet and mainnet.
- **Nonce compatibility**: A delegate action signed with nonce `N` on testnet is valid on mainnet whenever the mainnet access-key nonce is `< N`. For accounts that have never used a delegate action on mainnet, nonce 1 is always valid.
- **`max_block_height` overlap**: Block heights on mainnet and testnet are independent integers. A `max_block_height` of, say, `130_000_000` is valid on both networks simultaneously for an extended window.
- **Attacker access**: The signed payload travels off-chain from the user to a relayer. Any party in that path (a malicious relayer, a network observer, or the user themselves on a compromised device) can extract and replay it.

---

### Recommendation

Include a chain-binding field in the signed preimage. The cleanest approach is to add the genesis block hash (or a dedicated `chain_id` string from genesis config) to `DelegateAction` and `DelegateActionV2`, and incorporate it into `get_nep461_hash()`:

```rust
// In DelegateAction / DelegateActionV2, add:
pub chain_id: String,  // e.g. "mainnet", "testnet", or genesis_hash bytes

// In get_nep461_hash(), the SignableMessage already covers all fields via
// Borsh serialization of self, so adding chain_id to the struct is sufficient.
```

Alternatively, extend `SignableMessage` to carry a chain discriminant:

```rust
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub chain_id: &'a str,   // added
    pub msg: &'a T,
}
```

Either change is a protocol-breaking modification requiring a protocol version gate (similar to how `FixDelegatedDeterministicStateInit` was gated).

---

### Proof of Concept

1. Alice holds key `ed25519:AAAA...` on both `testnet` and `mainnet`, with mainnet access-key nonce = 0.
2. Alice signs a `DelegateAction` on testnet:
   ```
   sender_id:       alice.testnet
   receiver_id:     bob.testnet
   actions:         [Transfer { deposit: 10 NEAR }]
   nonce:           1
   max_block_height: 200_000_000   (far future on both networks)
   public_key:      ed25519:AAAA...
   ```
   The signature is `sig = ed25519_sign(key, SHA256(NEP366_discriminant || borsh(action)))`.
3. Attacker intercepts `sig` + the `DelegateAction` body from the relayer's off-chain channel.
4. Attacker constructs a mainnet transaction wrapping the same `SignedDelegateAction` (changing only `sender_id`/`receiver_id` to `alice.near`/`bob.near` — or keeping them if Alice uses the same account name on both networks).
5. Attacker submits to mainnet. `apply_delegate_action` calls `signed_delegate_action.verify()`:
   - The hash is recomputed as `SHA256(NEP366_discriminant || borsh(action))` — identical to the testnet hash.
   - Signature verifies against `ed25519:AAAA...`.
   - `max_block_height` check passes (mainnet height < 200_000_000).
   - Nonce check passes (mainnet ak_nonce = 0 < 1).
6. A new receipt is created on mainnet transferring 10 NEAR from `alice.near` to `bob.near` — an action Alice never authorized on mainnet.

The root cause is at: [1](#0-0) [3](#0-2)

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

**File:** core/primitives/src/signable_message.rs (L51-54)
```rust
pub struct MessageDiscriminant {
    /// The unique prefix, serialized in little-endian by borsh.
    discriminant: u32,
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

**File:** core/primitives/src/transaction.rs (L141-144)
```rust
    pub fn get_hash_and_size(&self) -> (CryptoHash, u64) {
        let bytes = borsh::to_vec(&self).expect("Failed to deserialize");
        (hash(&bytes), bytes.len() as u64)
    }
```

**File:** runtime/runtime/src/actions.rs (L430-448)
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
```
