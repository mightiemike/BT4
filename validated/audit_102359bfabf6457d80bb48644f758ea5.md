Based on my analysis, there's a valid structural analog to the reported vulnerability in nearcore's meta-transaction (NEP-366) implementation.

### Title
Missing chain/network binding in `SignedDelegateAction` payload allows cross-chain signature replay of meta-transactions - (File: `core/primitives/src/action/delegate.rs`, `core/primitives/src/signable_message.rs`)

### Summary
`DelegateAction`/`DelegateActionV2` (NEP-366 meta-transactions) are signed by the end user off-chain and later wrapped into a relayer's transaction. The signed payload (`sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, `public_key`) is hashed together with only a NEP-number discriminant, never a chain/network identifier such as `chain_id` or genesis hash.

### Finding Description
`SignedDelegateAction::verify` and `VersionedSignedDelegateAction::verify` compute a signature hash purely from `get_nep461_hash`, which serializes `SignableMessage{discriminant, msg}` where `discriminant` only encodes the NEP number (366/611), not any chain identity. [1](#0-0) [2](#0-1) 
The discriminant construction confirms it is bound only to the on-chain NEP number, with no chain/network component mixed in. [3](#0-2) 

Unlike a regular `SignedTransaction`, which includes a recent `block_hash` tying it to a specific chain's history, `DelegateAction` only carries a `max_block_height` (a plain integer bound), not a chain-specific hash: [4](#0-3) 

On-chain validation in `apply_delegate_action`/`validate_delegate_action_key` only checks the signature, `max_block_height` vs the local block height, `sender_id` match, and the access-key nonce — none of these are chain-specific: [5](#0-4) [6](#0-5) 

Because nothing in the signed hash binds the delegate action to a specific network, any `SignedDelegateAction` that is valid on one NEAR-protocol chain (e.g. a testnet, a forked/duplicate chain sharing the same genesis records and account/access-key state, or a chain that later re-splits) is equally valid on another chain, as long as the corresponding account, access key, and nonce line up there. A relayer (anyone, since the relayer role requires no special privilege — it's simply the outer transaction's signer/payer) can take an already-published `SignedDelegateAction` and resubmit it wrapped in a new transaction against a different chain.

### Impact Explanation
If the same account/access-key/nonce state exists on two chains (a realistic scenario for forked networks, disaster-recovery/backup chains, or short-lived chain splits sharing pre-fork state), an attacker (any relayer) can replay a user's previously authorized meta-transaction on the "wrong" chain without new authorization from the signer. This results in unauthorized execution of the delegated actions (e.g., token transfers, function calls) — a state transition the user only intended for one specific chain — constituting unauthorized value movement / an invalid state transition being accepted.

### Likelihood Explanation
Exploitation requires two NEAR-protocol chains to have overlapping account/access-key/nonce state at the time of replay (e.g., a network fork/split, or a duplicated testing/staging chain derived from the same genesis snapshot). This is a real, protocol-level architectural gap rather than a hypothetical one, but its likelihood is contingent on such multi-chain state overlap existing, which is not guaranteed in ordinary mainnet/testnet operation (separate genesis records). It is most acute during chain forks/migrations or when operators spin up parallel chains from a shared state snapshot.

### Recommendation
Include an explicit chain-binding value in the NEP-461/NEP-366 signable message — e.g., mix a `genesis_hash` or protocol-level `chain_id` into `SignableMessage` (similar to how `SignedTransaction` uses `block_hash` for replay protection) so that `DelegateAction` signatures are cryptographically scoped to one specific chain and cannot be replayed elsewhere, even when account/access-key state coincidentally matches.

### Proof of Concept
1. On chain A, a user signs a `DelegateAction{sender_id: "alice.near", receiver_id: "ft.near", actions: [ft_transfer to attacker], nonce: N, max_block_height: H, public_key: pk}` and hands the `SignedDelegateAction` to relayer R1, who wraps and submits it via `Action::Delegate` — verified purely via `SignedDelegateAction::verify` (no chain-specific data in the hash). [1](#0-0) 
2. Suppose chain B is a fork/duplicate of chain A's state (or a chain that re-diverged after a split) where "alice.near" still has access key `pk` with the same nonce value and account "ft.near" also exists with the same balance/state.
3. An attacker acting as relayer R2 takes the already-public `SignedDelegateAction` bytes (visible in chain A's block/receipt history) and wraps them into a new `Action::Delegate` transaction on chain B.
4. `apply_delegate_action` on chain B accepts it: signature verifies (same hash bytes, same key), `max_block_height` check passes (chain B's height still below H), `sender_id` matches, and `validate_delegate_action_key` nonce check passes (nonce still matches on chain B) — reference validation path: [7](#0-6) 
5. The transfer executes on chain B without the user ever authorizing an action on chain B specifically.

### Citations

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

**File:** core/primitives/src/action/delegate.rs (L210-214)
```rust
impl VersionedSignedDelegateAction {
    pub fn verify(&self) -> bool {
        let hash = self.delegate_action.get_nep461_hash();
        self.signature.verify(hash.as_ref(), self.delegate_action.public_key())
    }
```

**File:** core/primitives/src/signable_message.rs (L217-228)
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
```

**File:** docs/RuntimeSpec/Actions.md (L340-360)
```markdown
```rust
/// The struct a user creates and signs to create a meta transaction.
struct DelegateAction {
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

**File:** runtime/runtime/src/actions.rs (L579-616)
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
```
