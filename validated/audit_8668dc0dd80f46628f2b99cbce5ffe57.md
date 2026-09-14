### Title
Meta-transaction (`DelegateAction`/`DelegateActionV2`) signatures omit any chain/network identifier, enabling cross-fork replay - (File: `core/primitives/src/signable_message.rs`, `core/primitives/src/action/delegate.rs`)

### Summary
NEAR's meta-transaction (NEP-366/NEP-611) signature scheme signs a `MessageDiscriminant` (a fixed constant derived only from the NEP number) plus the borsh-serialized `DelegateAction`/`DelegateActionV2` payload. Nothing in this signed payload is bound to a specific network — there is no chain ID, genesis hash, or equivalent domain separator, analogous to Golom's `EIP712_DOMAIN_TYPEHASH` being fixed at deploy time from `chainid()`. If nearcore ever undergoes a chain-splitting hard fork (two live networks sharing identical historical state — accounts, access keys, nonces — at the fork height), a previously issued `SignedDelegateAction` remains valid and replayable on both resulting chains.

### Finding Description
The signing/verification logic is: [1](#0-0) [2](#0-1) 

The `MessageDiscriminant` is a static value derived purely from the NEP number (366 or 611), not from any chain-specific parameter: [3](#0-2) 

Verification of the signed delegate action, both V1 and V2, hashes only the discriminant plus the delegate-action fields (`sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, `public_key`) — none of which is network-specific: [4](#0-3) [5](#0-4) 

On-chain application in `apply_delegate_action` verifies the signature, checks `max_block_height`, matches `sender_id`, and validates/advances the access-key or gas-key nonce — again, all state that would be identical on both branches of a hard fork immediately after the split: [6](#0-5) [7](#0-6) 

This is the direct analog of the Golom finding: a signature-domain value (there, `chainId` baked into `EIP712_DOMAIN_TYPEHASH`; here, the complete absence of any chain-binding value in the NEP-366/611 signing domain) fails to distinguish between two chains that diverge after a fork, breaking replay protection across that fork boundary.

### Impact Explanation
If nearcore undergoes a contentious hard fork producing two independently live networks that share identical account/access-key/nonce state at the fork point (this has historical precedent in other blockchain ecosystems), any `SignedDelegateAction`/`VersionedSignedDelegateAction` created by a user before the fork — and still held by a relayer, or leaked/observed on one chain — can be resubmitted verbatim on the other chain. Because nonce, `max_block_height`, `sender_id`/`receiver_id`, and the public key all still validate identically on both chains post-fork, the meta-transaction executes successfully a second time, on the "wrong" chain, causing the sender's inner actions (transfers, contract calls, etc.) to be executed without a fresh authorization on that chain — i.e., unauthorized value movement / duplicate execution of a user-authorized action across chains.

### Likelihood Explanation
Low-to-moderate likelihood, contingent on an external event (a chain-splitting hard fork of nearcore), matching the same qualifier the original Code4rena judge used to keep the Golom finding at Medium ("very high-impact scenario, but it relies on the external factor of a hard fork... hard forks can and do happen"). No additional trust assumptions or privileged actors are needed beyond an ordinary relayer/meta-transaction holder replaying a previously valid signed payload.

### Recommendation
Bind the meta-transaction signing domain to the network by including a chain-specific value (e.g., `genesis_hash` or a network/chain identifier from `ApplyState`/genesis config) inside the `SignableMessage`/`MessageDiscriminant` construction in `core/primitives/src/signable_message.rs`, and validate it against the current chain's identifier in `apply_delegate_action` (`runtime/runtime/src/actions.rs`). This mirrors recomputing `EIP712_DOMAIN_TYPEHASH` with the live `chainId` rather than baking it in once.

### Proof of Concept
1. Alice signs a `DelegateAction` (nonce N, `max_block_height` H) via `SignedDelegateAction::sign` and hands it to Relayer R, per [8](#0-7) .
2. Before block H, the network undergoes a hard fork; two chains A and B now exist, both containing Alice's account with the same access-key nonce N-1 (not yet advanced).
3. R submits the `SignedDelegateAction` inside a transaction on chain A; `apply_delegate_action` verifies the signature, checks `max_block_height`, and advances the nonce to N, executing Alice's inner actions per [9](#0-8) .
4. R (or anyone else with the same bytes) submits the identical `SignedDelegateAction` on chain B. Since chain B has the same pre-fork nonce/state and nothing in the signed payload differs between chains, verification passes again and Alice's inner actions execute a second time on chain B without any new authorization from Alice.

### Citations

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

**File:** core/primitives/src/action/delegate.rs (L210-220)
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

**File:** runtime/runtime/src/actions.rs (L574-646)
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
```
