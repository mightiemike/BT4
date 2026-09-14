Based on my investigation, I have enough evidence to construct a valid analog.

### Title
Missing chain/network domain separator in `DelegateAction` (NEP-366 meta-transaction) signature allows cross-network signature replay - (File: `core/primitives/src/action/delegate.rs`, `core/primitives/src/signable_message.rs`)

### Summary
The `SignedDelegateAction`/`VersionedSignedDelegateAction` structures that back NEAR's meta-transactions (NEP-366/NEP-611) are signed over a hash that only binds `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key` via `get_nep461_hash()`. Unlike regular `SignedTransaction`s, which bind freshness/replay-protection to a specific `block_hash` from the chain's actual block history [1](#0-0) , the delegate-action payload contains no chain-specific or genesis-specific datum at all — only a raw `max_block_height` integer [2](#0-1) . This is exactly the missing-domain-separator bug class described in the external report (no chain ID in the signed message), reachable directly by an unprivileged meta-transaction sender/relayer.

### Finding Description
`SignedDelegateAction::verify()` and `VersionedSignedDelegateAction::verify()` recompute `get_nep461_hash()` from the `DelegateAction`/`DelegateActionV2` fields and check the signature against it [3](#0-2) [4](#0-3) . The hash is produced by `SignableMessage`, which only prefixes a NEP-number discriminant (`366` or `611`) before borsh-serializing the message body [5](#0-4) [6](#0-5) . Nothing in this discriminant or in `DelegateAction`/`DelegateActionV2` encodes a chain identifier, genesis hash, or recent block hash — the only "freshness" gate is `max_block_height`, a plain integer compared against the local `apply_state.block_height` [7](#0-6) .

On-chain replay protection for the *same* chain is provided purely by the access key's stored `nonce` (or a gas key's nonce row), checked and advanced in `validate_delegate_action_key` [8](#0-7) [9](#0-8) . Because the nonce/height state lives in each chain's own trie, any second nearcore-based network instance that shares the same account state at some point in time (e.g., a network bootstrapped/forked from a state snapshot of another network, a private/enterprise deployment reusing production genesis state, or a testnet whose accounts and access-key nonces mirror another environment) will independently accept the identical `SignedDelegateAction` bytes as valid, since the signature check, nonce check, and `max_block_height` check are all local, chain-relative, and contain no cross-chain-distinguishing material.

This mirrors the reported `HardenedTopupProxy` bug precisely: the signed payload is deploy/chain-agnostic, so a signature valid on chain A remains valid verbatim on chain B whenever B's local state (nonce, block height) has not yet diverged past the values baked into the signature.

### Impact Explanation
An attacker who observes a `SignedDelegateAction` (e.g., a relayer, or anyone who intercepts one off-chain before it's consumed) can replay it verbatim against any other nearcore-based chain sharing the signer's account/access-key state (forked state, cloned genesis, or a second deployment using the same key material), causing the delegated actions (which can include token transfers, `FunctionCall`, `AddKey`, etc., limited only by the signer's access key permissions) to execute a second time on the other chain without the signer's renewed authorization. This is unauthorized value movement / duplicated action execution triggered purely by a relayer/meta-transaction sender submitting an already-once-used signed payload to a second chain instance.

### Likelihood Explanation
Likelihood is moderate: it requires two independently-operated nearcore chains to share overlapping account/access-key state (e.g., a chain forked or cloned from another chain's snapshot, which is a documented and common operational pattern for testnets/private deployments). No malicious validator, leaked key, or network-layer manipulation is required — the replay is a normal, unprivileged transaction submission (`Action::Delegate`/`Action::DelegateV2`) that any relayer can make.

### Recommendation
Bind the NEP-366/NEP-611 `SignableMessage` discriminant (or the `DelegateAction`/`DelegateActionV2` struct itself) to a chain-specific value — e.g., include the genesis hash / chain id in the hashed payload, or require `DelegateAction` to reference a recent `block_hash` the same way `Transaction` does — so that a signature produced for one nearcore network cannot be replayed as valid on a different network instance even when nonce and block-height state coincidentally overlap.

### Proof of Concept
1. Sign a `DelegateAction` (nonce `N`, `max_block_height = H`) with account `alice`'s key on chain A, producing `SignedDelegateAction_A` via `SignedDelegateAction::sign` [10](#0-9) .
2. Stand up chain B whose state was forked/cloned from chain A at a point where `alice`'s access key nonce is still `< N` and current block height `< H` (e.g., a testnet bootstrapped from a mainnet snapshot).
3. Submit the identical `SignedDelegateAction_A` bytes wrapped in a transaction on chain B. `apply_delegate_action` verifies the signature successfully (no chain-binding data differs) [11](#0-10) , `validate_delegate_action_key` accepts the nonce because chain B's stored nonce is still below `N` [12](#0-11) , and the delegated actions execute a second time, on a chain the signer never intended to authorize.

### Citations

**File:** docs/RuntimeSpec/Scenarios/FinancialTransaction.md (L31-34)
```markdown
```
Transaction {
    signer_id: "alice_near",
    public_key: "ed25519:32zVgoqtuyRuDvSMZjWQ774kK36UTwuGRZMmPsS6xpMy",
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

**File:** core/primitives/src/action/delegate.rs (L92-95)
```rust
    pub fn sign(singer: &Signer, delegate_action: DelegateAction) -> Self {
        let signature = singer.sign(delegate_action.get_nep461_hash().as_bytes());
        Self { delegate_action, signature }
    }
```

**File:** core/primitives/src/action/delegate.rs (L210-214)
```rust
impl VersionedSignedDelegateAction {
    pub fn verify(&self) -> bool {
        let hash = self.delegate_action.get_nep461_hash();
        self.signature.verify(hash.as_ref(), self.delegate_action.public_key())
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

**File:** runtime/runtime/src/actions.rs (L453-477)
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
```

**File:** runtime/runtime/src/actions.rs (L478-482)
```rust
    let delegate_action = signed_delegate_action.delegate_action();
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L574-656)
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

    if delegate_nonce.nonce() <= current_nonce {
        result.result = Err(ActionErrorKind::DelegateActionInvalidNonce {
            delegate_nonce: delegate_nonce.nonce(),
            ak_nonce: current_nonce,
        }
        .into());
        return Ok(());
    }

```

**File:** runtime/runtime/src/actions.rs (L729-746)
```rust
    match nonce_update {
        DelegateNonceUpdate::AccessKey => {
            access_key.nonce = delegate_nonce.nonce();
            set_access_key(state_update, sender_id.clone(), public_key.clone(), &access_key);
        }
        DelegateNonceUpdate::GasKey { nonce_index } => {
            set_gas_key_nonce(
                state_update,
                sender_id.clone(),
                public_key.clone(),
                nonce_index,
                delegate_nonce.nonce(),
            );
        }
    }

    Ok(())
}
```
