### Title
`DelegateAction`/`DelegateActionV2` signature payload omits any chain-binding value (no `block_hash`/genesis identity), enabling cross-chain meta-transaction signature replay - (File: `core/primitives/src/action/delegate.rs`, `core/primitives/src/signable_message.rs`)

### Summary
The NEP-366/NEP-611 meta-transaction signing scheme hashes only `sender_id, receiver_id, actions, nonce, max_block_height, public_key` (tagged by a NEP discriminant) to produce the hash a user signs for a `DelegateAction`/`DelegateActionV2`. Unlike a normal `Transaction`, which embeds `block_hash` pinning the signature to a specific chain's recent history, the delegate-action payload contains no chain-specific value at all (no genesis hash, no `block_hash`, no chain id analog). This is structurally the same root cause as the reported DODO GSP issue: signing material lacking a chain-binding domain separator, so a signature produced for one chain remains valid and replayable on any other chain that shares compatible account/access-key state.

### Finding Description
`DelegateAction` is defined with only these signed fields: [1](#0-0) 

The signature itself is computed purely from these fields plus a fixed NEP discriminant, with no chain-specific salt: [2](#0-1) [3](#0-2) 

Contrast this with the ordinary `Transaction`, whose `block_hash` field is explicitly documented as chain-specific replay protection tying the signed payload to a particular chain's recent blocks: [4](#0-3) 

The project's own tooling confirms `block_hash` is what makes a signed transaction chain-specific: the `tools/mirror` utility, which forks a chain's state onto a second ("target") chain, explicitly notes that the original transactions cannot be replayed onto the forked chain unmodified because `block_hash` will be rejected — it must be freshly resigned: [5](#0-4) 

However, this protection lives only in the outer `SignedTransaction` that the *relayer* constructs and signs — not in the inner `DelegateAction` that the *end-user* signs to authorize a meta transaction. Verification of the inner signature only checks the NEP-461 hash and sender/receiver/nonce/height fields, never anything chain-specific: [6](#0-5) 

Nonce validation is scoped to the account's access key state, which is fork-local and can be identical across two chains that share a genesis or recent history (contentious hard fork, or any nearcore-based network bootstrapped from the same state, e.g. via `dump-state`/mirror workflows or a testnet reset from mainnet state): [7](#0-6) 

### Impact Explanation
A malicious or compromised relayer that captures a user's `SignedDelegateAction` (or `VersionedSignedDelegateAction`) intended for use on chain A can wrap that identical payload in a fresh outer `SignedTransaction` (which it itself signs and which only needs a valid `block_hash` on chain B) and submit it to chain B. As long as chain B has compatible state for the sender's account/access-key/nonce (true immediately after a hard fork, or for any nearcore network derived from a shared genesis/state dump), the runtime will accept the same user-signed `DelegateAction` and execute the delegated actions (e.g. `AddKeyAction`, transfers via inner actions, contract calls) on chain B without the user's consent for that specific chain. This is exactly the "signature replay across forked chains" scenario in the external report, translated to NEAR's meta-transaction primitive: unauthorized execution of user-authorized actions (potential fund movement, unauthorized key addition, unauthorized contract calls) on a sibling/forked network.

### Likelihood Explanation
Likelihood depends on (a) a hard fork or state-sharing deployment actually occurring, and (b) a relayer being willing to misuse a captured `SignedDelegateAction` (the relayer is inherently untrusted-but-cooperating in the NEP-366 model — it must see and relay the signed payload to submit it at all, so it always has the material needed to replay it elsewhere). nearcore is explicitly designed to be redeployed as multiple independent networks from shared genesis/state (as shown by the `tools/mirror` forking workflow), so the "second chain with compatible state" precondition is a supported, documented operational pattern rather than a purely theoretical event.

### Recommendation
Bind the delegate-action signing payload to chain identity, e.g. include a `genesis_hash`/network id (analogous to the outer transaction's `block_hash`), or otherwise fold a persistent chain-identifying value into the NEP-461 message discriminant/hash so that a `DelegateAction` signed for one nearcore-based network cannot be verified as valid on another network that happens to share compatible account/access-key state.

### Proof of Concept
1. Operator forks a nearcore chain (hard fork, or via `neard view-state dump-state` + `tools/mirror`-style genesis/state export) producing chain B with account/access-key state identical to chain A at the fork point.
2. Alice signs a `DelegateAction` (e.g., an `AddKeyAction` delegate) intended for chain A and hands it to Relayer, who wraps and submits it in chain A's outer `SignedTransaction`; the action executes on chain A.
3. Relayer takes the exact same `SignedDelegateAction`/`VersionedSignedDelegateAction` bytes, wraps them in a new outer `SignedTransaction` addressed to chain B (using chain B's own current `block_hash`, which chain B's own verifier accepts), and submits it to chain B.
4. Because `SignedDelegateAction::verify()` / `VersionedSignedDelegateAction::verify()` only check the NEP-461 hash over `sender_id, receiver_id, actions, nonce, max_block_height, public_key` (`core/primitives/src/action/delegate.rs:83-90`, `210-214`) and `validate_delegate_action_key` only checks the sender's access-key nonce state on chain B (`runtime/runtime/src/actions.rs:579-646`), the delegate action verifies and executes on chain B as well — even though Alice never intended to authorize it there.

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

**File:** docs/architecture/how/tx_routing.md (L31-34)
```markdown
**Fun fact:** the `Transaction` object also contains some fields to prevent
attacks: like `nonce` to prevent replay attack, and `block_hash` to limit the
validity of the transaction (it must be added within
`transaction_validity_period` (defined in genesis) blocks of `block_hash`).
```

**File:** tools/mirror/README.md (L14-21)
```markdown
The first approach we might try is to just send the source chain
transactions byte-for-byte unaltered to the target chain. This almost
works, but not quite, because the `block_hash` field in the
transactions will be rejected. This means we have no choice but to
replace the accounts' public keys in the original forked state, so
that we can sign transactions with a valid `block_hash` field. So the
way we'll use this is that we'll generate the forked state from the
source chain using the usual `dump-state` command, and then run:
```

**File:** runtime/runtime/src/actions.rs (L474-497)
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
```

**File:** runtime/runtime/src/actions.rs (L579-646)
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
```
