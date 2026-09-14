### Title
`DelegateAction` / `DelegateActionV2` signed meta-transaction payload lacks chain/network domain separation, enabling cross-chain signature replay - (File: core/primitives/src/action/delegate.rs, core/primitives/src/signable_message.rs)

### Summary
NEAR's ordinary transactions bind a signature to a specific chain fork via `TransactionV0::block_hash` (a recent block hash on the chain the signer intends), which expires quickly and differs across independent chains/networks. Meta-transactions (`DelegateAction`/`DelegateActionV2`, NEP-366/NEP-611) instead sign a payload that contains only a plain integer `max_block_height` and a NEP-tagged discriminant — never a chain-identifying hash (genesis hash / chain ID / block hash). This mirrors the reported `NodeRegistry.registerNodeFor()` flaw: the signed data omits the equivalent of `registryID`/contract address, so a signature valid on one deployment can be replayed on another.

### Finding Description
`SignedDelegateAction::verify()` and `VersionedSignedDelegateAction::verify()` hash the `DelegateAction`/`DelegateActionV2` together with a `MessageDiscriminant` that only encodes the NEP number (366 or 611) via `SignableMessage`/`MessageDiscriminant::new_on_chain` [1](#0-0) . The fields committed to are `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, `public_key` [2](#0-1)  — none of which identify a specific chain/network. Compare this to ordinary transactions, which embed `block_hash: CryptoHash`, "the hash of the block in the blockchain on top of which the given transaction is valid" [3](#0-2) , giving them implicit protection against replay across forks/independent chains since block hashes differ per chain and quickly become stale.

The only freshness/expiry check applied to a `DelegateAction` in `apply_delegate_action` is `apply_state.block_height > delegate_action.max_block_height()` [4](#0-3) , and nonce validation is scoped strictly to the access key state of `sender_id`/`public_key` on the chain executing the receipt [5](#0-4) . Neither check ties the signature to a particular chain: `max_block_height` is a trivially-satisfiable integer on any chain with sufficient height, and access-key nonce state is chain-local and starts at 0 independently on every chain (this is especially relevant for implicit accounts, whose `AccountId` is derived deterministically from the public key and is therefore often identical across independently-run networks, e.g., a testnet and mainnet, or two forks sharing the same genesis).

### Impact Explanation
If the same `sender_id`/`public_key`/`receiver_id` combination (most plausible with NEAR implicit accounts) exists on two different NEAR-based chains — e.g., mainnet and a fork/private deployment, or before/after a network split — a `SignedDelegateAction` a user intended to authorize on one chain can be relayed and executed unmodified on the other chain, as long as the nonce there hasn't already advanced past the signed value and the block height is below `max_block_height`. Because a relayer prepays the fees/gas and the inner actions execute with `sender_id` as predecessor, this allows unauthorized execution of the signed actions (transfers, function calls, key changes, etc.) against the sender's account on a chain the signer never intended, i.e., unauthorized value movement / invalid state transition from the signer's perspective, driven entirely by a relayer (an unprivileged transaction submitter) replaying a previously-obtained signed meta-transaction.

### Likelihood Explanation
Exploitability requires: (1) a relayer or any party in possession of a validly-signed `SignedDelegateAction`, and (2) the existence of a second reachable NEAR-protocol chain where the same `sender_id`, `public_key`, and `receiver_id` exist with an access-key nonce still below the signed nonce. This is a realistic condition for implicit accounts and for forked/duplicated networks (testnets copied from mainnet state, disaster-recovery forks, or app-specific NEAR-compatible chains), making this a credible, moderate-likelihood scenario rather than a purely theoretical one — comparable to the original report's reasoning about registryID-less signatures being replayable "to multiple registries or chains."

### Recommendation
Include a chain-identifying value (e.g., the genesis hash or a protocol/chain ID) in the `DelegateAction`/`DelegateActionV2` payload that is committed to by `get_nep461_hash()`, and validate it against the executing chain's identity in `apply_delegate_action`, analogous to how `TransactionV0::block_hash` binds ordinary transactions to a specific chain state.

### Proof of Concept
Conceptual PoC (not executed, protocol-level reasoning only):
1. On Chain A, Alice signs `DelegateAction { sender_id: "abcd...ef" (implicit), receiver_id: "bob.near", actions: [Transfer], nonce: N, max_block_height: H, public_key: pk }` via `SignedDelegateAction::sign` [6](#0-5) , intending it for submission only on Chain A.
2. A relayer instead submits the identical `SignedDelegateAction` bytes as an action on Chain B, where the same implicit account `abcd...ef` also exists with an access key of the same `public_key` and current nonce `< N`, and Chain B's current block height is `< H`.
3. `apply_delegate_action` on Chain B verifies the signature successfully (nothing in the hash differs between chains), passes the `max_block_height` check, passes `sender_id` match, and `validate_delegate_action_key` accepts the nonce since it only compares against Chain B's local nonce state [7](#0-6) .
4. The transfer/actions execute on Chain B against Alice's account without her having ever authorized an action there.

### Citations

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

**File:** core/primitives/src/action/delegate.rs (L92-95)
```rust
    pub fn sign(singer: &Signer, delegate_action: DelegateAction) -> Self {
        let signature = singer.sign(delegate_action.get_nep461_hash().as_bytes());
        Self { delegate_action, signature }
    }
```

**File:** core/primitives/src/transaction.rs (L36-51)
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

**File:** runtime/runtime/src/actions.rs (L579-655)
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

    if delegate_nonce.nonce() <= current_nonce {
        result.result = Err(ActionErrorKind::DelegateActionInvalidNonce {
            delegate_nonce: delegate_nonce.nonce(),
            ak_nonce: current_nonce,
        }
        .into());
        return Ok(());
    }
```
