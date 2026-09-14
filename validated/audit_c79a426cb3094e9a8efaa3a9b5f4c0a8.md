### Title
`DelegateAction`/`DelegateActionV2` meta-transaction signatures lack a chain/network domain separator, enabling cross-chain replay - (File: `core/primitives/src/action/delegate.rs`)

### Summary
NEAR's meta-transaction (NEP-366) signature scheme signs a `DelegateAction` using only a NEP-461 discriminant tag plus the action's own fields (sender, receiver, actions, nonce, max_block_height, public_key). No genesis hash, network id, or chain-specific value is included in the signed payload, so a signature produced for one NEAR network is byte-for-byte valid on any other NEAR-compatible network where the same account/access-key state exists.

### Finding Description
`SignedDelegateAction::verify` and `VersionedSignedDelegateAction::verify` recompute a hash over the `SignableMessage` wrapper (a 4-byte NEP-461 discriminant + the borsh-serialized action) and check the signature against that hash and the embedded `public_key`: [1](#0-0) [2](#0-1) 

The `DelegateAction`/`DelegateActionV2` structs that get hashed contain no chain/network identifier at all — only `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key`: [3](#0-2) [4](#0-3) 

The discriminant used for domain separation (`SignableMessage`/`MessageDiscriminant`) only encodes a NEP number (366 or 611) to distinguish message *types*, not a chain or genesis identifier: [5](#0-4) [6](#0-5) 

At apply time, `apply_delegate_action` verifies the signature, checks `max_block_height` against the local chain's current block height, checks `sender_id` matches the tx receiver, and validates/increments the access key nonce — but it never checks any chain/genesis identifier because none exists in the signed payload: [7](#0-6) 

By contrast, ordinary `SignedTransaction`s in this codebase incorporate a recent `block_hash` from the sending chain as part of the signed payload, which is chain-specific and provides an implicit domain separator; `DelegateAction` relies only on a plain `max_block_height` integer and an access-key `nonce`, both of which are not chain-unique values — they can coincide across two independently-run NEAR-protocol networks/chains (e.g., a forked/duplicated deployment, a testing network cloned from mainnet state, or a redeployed chain) that happen to share account ids, access keys, and nonce state.

### Impact Explanation
If the same account id and access key/nonce state exist on two different NEAR-protocol networks (for example, a forknet, a private deployment cloned from a snapshot, or any scenario where account/key state is duplicated across chains for migration/testing), a `SignedDelegateAction` authorized by a user for actions on chain A can be replayed verbatim by any relayer on chain B, since `verify()` only checks the signature over data that contains no chain-binding value. Because a `DelegateAction` can carry arbitrary `NonDelegateAction`s (transfers, key additions, function calls, etc.) executed with the sender's authority, a successful replay results in unauthorized value movement or unauthorized account modification on the second chain without a fresh authorization from the account owner.

### Likelihood Explanation
Exploitation requires that the attacker (a relayer) has access to a signed `DelegateAction` and that matching account/key/nonce state exists on another chain with the same protocol. This is a real operational scenario for NEAR (e.g., testnets/forknets bootstrapped from mainnet state snapshots, chain migrations, or any dual-deployment setup sharing genesis account data) — but it is not automatically true for every pair of NEAR networks, since nonces must also line up. This reduces likelihood relative to the archetypal EIP-712 replay bug but the attack surface (missing chain domain separator) is structurally identical and reachable purely by an unprivileged relayer submitting a transaction, with no validator/node compromise needed.

### Recommendation
Add an explicit chain/network domain separator to the signed payload — e.g., include the genesis hash or a configured network id as a field in `DelegateAction`/`DelegateActionV2` (or fold it into the `SignableMessage` discriminant/prefix used in `get_nep461_hash`) — and validate it against the runtime's own genesis/network id in `apply_delegate_action` before accepting the signature, mirroring how ordinary transactions bind to a chain-specific `block_hash`.

### Proof of Concept
1. Deploy/clone two NEAR-protocol networks (chain A and chain B) that share the same genesis account state, including account `alice.near` with access key `K` at nonce `N`.
2. `alice.near` signs a `DelegateAction{sender_id: alice.near, receiver_id: bob.near, actions: [Transfer(...)], nonce: N+1, max_block_height: H, public_key: K}` intending it to be relayed only on chain A.
3. A relayer submits the resulting `SignedDelegateAction` in a transaction on chain A — it executes as intended.
4. The same relayer (or any third party who obtained the signed payload) submits the identical `SignedDelegateAction` bytes in a transaction on chain B. Because `SignedDelegateAction::verify` (`core/primitives/src/action/delegate.rs:83-96`) only checks the NEP-461 hash/signature/public key with no chain-binding data, and `apply_delegate_action` (`runtime/runtime/src/actions.rs:453-497`) only checks `max_block_height` against chain B's own height and the nonce against chain B's own access-key state (both of which line up since the state was cloned), the transfer executes again on chain B without `alice.near` ever authorizing an action there.

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

**File:** core/primitives/src/action/delegate.rs (L210-219)
```rust
impl VersionedSignedDelegateAction {
    pub fn verify(&self) -> bool {
        let hash = self.delegate_action.get_nep461_hash();
        self.signature.verify(hash.as_ref(), self.delegate_action.public_key())
    }

    pub fn sign(signer: &Signer, delegate_action: VersionedDelegateActionPayload) -> Self {
        let signature = signer.sign(delegate_action.get_nep461_hash().as_bytes());
        Self { delegate_action, signature }
    }
```

**File:** core/primitives/src/signable_message.rs (L17-25)
```rust
// TODO: consider making these public once there is an approved standard.
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

**File:** runtime/runtime/src/actions.rs (L453-497)
```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    // The inner delegate signature is verified below, here on the receiver shard.
    // Meter its verification compute against this shard's `compute_limit`; the gas
    // for it was already burnt at tx conversion on the signer shard. Without the
    // fix the compute is instead mis-charged on the signer shard (which never runs
    // this verify), letting the work escape the receiver shard's budget. See
    // `signature_verification_cost`.
    if apply_state.config.wasm_config.fix_ml_dsa_cost_charging {
        let verify_compute = delegate_signature_verification_compute(
            &apply_state.config.fees,
            signed_delegate_action.delegate_action().public_key(),
        );
        result.compute_usage = safe_add_compute(result.compute_usage, verify_compute)?;
    }
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
