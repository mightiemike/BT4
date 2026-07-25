### Title
DelegateAction Signed Payload Lacks Chain Binding — Cross-Chain Replay of Meta Transactions - (File: `core/primitives/src/action/delegate.rs`)

### Summary

`DelegateAction` (NEP-366 meta transactions) and `DelegateActionV2` (NEP-611) produce a signed hash that contains no chain identifier. A signed `DelegateAction` created for testnet can be replayed on mainnet (or any other NEAR network) by any party who obtains the off-chain payload, provided the sender's nonce on the target chain is lower than the one in the action and `max_block_height` has not yet elapsed on that chain.

### Finding Description

Regular NEAR `SignedTransaction` objects include a `block_hash` field that is chain-specific: a block hash from mainnet does not exist in testnet's chain store, so `check_transaction_validity_period` rejects it with `InvalidTxError::Expired`. This gives ordinary transactions implicit chain binding.

`DelegateAction` has no equivalent binding. Its signed payload is:

```
hash( borsh( MessageDiscriminant(1<<30 + 366) || DelegateAction { sender_id, receiver_id, actions, nonce, max_block_height, public_key } ) )
``` [1](#0-0) 

The `MessageDiscriminant` is a fixed constant derived from the NEP number, identical on every NEAR network. [2](#0-1) 

`max_block_height` is a block height, not a chain-specific commitment. Block heights on mainnet and testnet are independent monotonically-increasing counters; a height that is in the future on one chain may also be in the future on another.

The same omission exists in `DelegateActionV2` / `VersionedDelegateActionPayload::get_nep461_hash()`: [3](#0-2) 

Signature verification in `SignedDelegateAction::verify()` and `VersionedSignedDelegateAction::verify()` only checks the hash of the chain-agnostic payload against the public key: [4](#0-3) 

The runtime's `apply_delegate_action` / `validate_delegate_action_key` enforces nonce ordering and `max_block_height` but performs no chain-identity check: [5](#0-4) 

By contrast, the ETH-implicit wallet contract (`near-wallet-contract`) explicitly validates `tx.chain_id == CHAIN_ID` (a compile-time constant differing between mainnet=397 and testnet=398) before accepting any Ethereum-style transaction: [6](#0-5) 

This asymmetry means the native NEAR meta-transaction path has weaker cross-chain replay protection than the Ethereum-emulation path in the same codebase.

### Impact Explanation

An attacker who obtains a `SignedDelegateAction` that Alice created for testnet can submit it on mainnet (wrapped in a relayer transaction) if:

1. Alice's account exists on mainnet with the same public key.
2. Alice's access-key nonce on mainnet is strictly less than the nonce embedded in the `DelegateAction`.
3. The current mainnet block height is below `max_block_height`.

If all three conditions hold, the runtime accepts the action as valid and executes the inner actions (e.g., `TransferAction`, `FunctionCallAction`) on Alice's behalf on mainnet without her consent. This constitutes an unauthorized transaction and potential loss of funds.

### Likelihood Explanation

- Developers routinely use the same ED25519 key pair on testnet and mainnet.
- Relayers receive `SignedDelegateAction` objects off-chain; a malicious or compromised relayer, or any network observer, can forward the payload to a different chain.
- Nonce divergence between chains is common early in an account's life (e.g., testnet nonce is 5, mainnet nonce is 2 — the testnet-signed action with nonce 3 is valid on mainnet).
- `max_block_height` is typically set to current height + a small buffer (e.g., 100 blocks), which is almost always in the future on both chains simultaneously.

### Recommendation

Include the genesis `chain_id` string (or its hash) in the `DelegateAction` / `DelegateActionV2` signed payload. This mirrors the approach already used by the wallet contract and is the standard defense against cross-chain replay (analogous to EIP-155 in Ethereum). Concretely:

- Add a `chain_id: String` field to `DelegateAction` and `DelegateActionV2`.
- Include it in the borsh-serialized payload that `get_nep461_hash()` hashes.
- Validate at execution time that the `chain_id` in the action matches the node's genesis `chain_id`.

Because this changes the signed wire format, it requires a protocol version gate.

### Proof of Concept

1. Alice creates a `DelegateAction` on testnet with `nonce = 5`, `max_block_height = testnet_height + 100`, and `actions = [TransferAction { deposit: 10 NEAR }]` to receiver Bob.
2. Alice signs it: `signature = sign(get_nep461_hash(&delegate_action))`. The hash covers no chain identifier.
3. An attacker intercepts the `SignedDelegateAction` before it reaches the testnet relayer.
4. The attacker wraps it in a mainnet `SignedTransaction` (the attacker pays gas as the relayer) addressed to Alice's mainnet account.
5. The mainnet runtime calls `apply_delegate_action` → `validate_delegate_action_key`:
   - Signature check passes (same key, same hash — chain-agnostic).
   - Nonce check passes if Alice's mainnet access-key nonce < 5.
   - Height check passes if mainnet block height < `max_block_height`.
6. The runtime executes `TransferAction { deposit: 10 NEAR }` from Alice's mainnet account to Bob, draining Alice's mainnet funds without her authorization.

Relevant code path: [7](#0-6) [8](#0-7) [9](#0-8) [5](#0-4)

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

**File:** core/primitives/src/signable_message.rs (L97-107)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L328-330)
```rust
    if tx.chain_id != Some(CHAIN_ID) {
        return Err(Error::Relayer(RelayerError::InvalidChainId));
    }
```
