### Title
Cross-chain/cross-network replay of NEP-366 `SignedDelegateAction` meta-transactions due to missing chain/genesis binding in the signed payload - (File: `core/primitives/src/action/delegate.rs`, `core/primitives/src/signable_message.rs`)

### Summary
`DelegateAction`/`DelegateActionV2` (NEP-366 meta transactions) are signed via `get_nep461_hash()`, which only hashes `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key` [1](#0-0) . Unlike ordinary `SignedTransaction`s, which are bound to a specific chain by a recent `block_hash` (a field the codebase's own `tools/mirror` documents as chain-specific and therefore un-reusable across networks) [2](#0-1) , a `SignedDelegateAction`/`VersionedSignedDelegateAction` carries no chain, genesis, or network identifier at all. Its signature therefore remains valid on any NEAR network that happens to share the same account/access-key/nonce state for the signer — which is exactly the situation produced by NEAR's own supported chain-forking/migration workflow (`neard view-state dump-state` + `tools/mirror`).

### Finding Description
The verification logic for both delegate action versions only checks the NEP-461 signature over the action's own fields: [3](#0-2) [4](#0-3) 

The hash is computed purely from `SignableMessage { discriminant, msg: &delegate_action }`, where `discriminant` only encodes the NEP number (366/611), not any network/chain identifier [5](#0-4) .

At apply time, `apply_delegate_action` and `validate_delegate_action_key` re-derive the same hash and check only: signature validity, `max_block_height` vs current block height, `sender_id` match, and that the delegate `nonce`/`gas key nonce` is strictly greater than what is currently stored for that `(sender_id, public_key)` pair on whatever chain is executing the receipt [6](#0-5) [7](#0-6) . None of these checks reference a chain/genesis identifier.

By contrast, regular `SignedTransaction`s are protected from cross-chain replay because they embed a recent `block_hash`, which is unique per chain; the repo's own `tools/mirror` tool explicitly states it must strip and re-sign transactions with mapped keys specifically because "the `block_hash` field in the transactions will be rejected" on a different (forked) chain [2](#0-1) . Critically, `tools/mirror`'s action-mapping code has a `// TODO: handle delegate actions` and falls through to forwarding `Action::Delegate`/`Action::DelegateV2` byte-for-byte unmodified [8](#0-7) , i.e., the tool itself demonstrates that a `SignedDelegateAction`'s signature is chain-agnostic and continues to validate once wrapped inside a freshly-signed outer transaction targeting the forked/target chain.

Because meta transactions are explicitly designed so that any third-party "relayer" (an untrusted, permissionless role) can take a user's off-chain `SignedDelegateAction` and wrap/submit it inside their own transaction [9](#0-8) , an attacker acting as relayer can capture a `SignedDelegateAction` a user produced/intended for one network and instead wrap and submit it on a second network that shares the signer's account/access-key/nonce state (e.g., a mainnet-forked testnet, a mocknet, a disaster-recovery/migration fork, or any two networks bootstrapped from the same genesis snapshot — a scenario nearcore natively supports).

### Impact Explanation
If a signer's `DelegateAction` (e.g., transferring funds, adding a full-access key, or invoking a function call with a deposit) is replayed on a second network where the same account state still exists, the attacker-relayer causes unauthorized execution of the delegated actions on that second chain — unauthorized value movement (transfers/deposits) or unauthorized privilege escalation (`AddKey` with full access), exactly analogous to the Harpie `changeRecipientAddress` cross-chain replay where a signature intended for one network is legitimately valid and exploitable on another. This is a concrete state-divergence/unauthorized-value-movement class issue reachable purely from a single relayed transaction, satisfying the "unauthorized value movement" acceptance criterion.

### Likelihood Explanation
Exploitability depends on the existence of a second network sharing matching account/key/nonce state with the source network. This is not a hypothetical: nearcore ships and documents exactly this pattern (fork-from-genesis + mirror traffic) for migrations/mocknet/testnet-from-mainnet-state setups, and the mirror tool's own comments acknowledge it does not remap delegate actions. Any relayer (an inherently permissionless, unprivileged role in the meta-transaction design) holding a captured `SignedDelegateAction` intended for one such network can immediately attempt to replay it on the sibling network without needing any private key material, only the public signed payload that was already broadcast off-chain to relayers.

### Recommendation
Bind the delegate-action signature to the specific chain by including a chain/genesis identifier (e.g., `genesis_hash`/`chain_id` or the runtime's `ChainId`) inside the hashed `DelegateAction`/`DelegateActionV2` payload (or as part of the `SignableMessage` discriminant/prefix), analogous to how `SignedTransaction.block_hash` binds ordinary transactions to a specific chain. Validation in `apply_delegate_action`/`validate_delegate_action_key` should then reject any `SignedDelegateAction` whose embedded chain identifier doesn't match the executing chain's own identifier.

### Proof of Concept
1. Network A and Network B share identical account state (e.g., Network B was created via `neard view-state dump-state` from Network A, or both use the mirror tool's `prepare`/`run` workflow described in `tools/mirror/README.md`).
2. Alice signs a `DelegateAction { sender_id: "alice", receiver_id: "ft.near", actions: [ft_transfer(attacker, 1000)], nonce: N, max_block_height: H, public_key: alice_pk }` using `SignedDelegateAction::sign` [10](#0-9)  intending it to be relayed only on Network A.
3. An attacker acting as relayer intercepts this `SignedDelegateAction` (it necessarily travels off-chain in plaintext to some relayer) and instead wraps it in a fresh outer `SignedTransaction` (with a valid, current `block_hash` for Network B) and submits it to Network B.
4. On Network B, `apply_delegate_action`/`validate_delegate_action_key` find that `alice`'s access key with `public_key = alice_pk` and stored nonce `< N` still exists (because Network B forked from Network A's state), `apply_state.block_height <= H`, and the NEP-461 signature verifies (since it never included any chain identifier) — the ft_transfer executes on Network B even though Alice never intended or authorized this on Network B.

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

**File:** runtime/runtime/src/actions.rs (L574-655)
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

**File:** tools/mirror/src/lib.rs (L1160-1163)
```rust
                }
                // TODO: handle delegate actions
                _ => actions.push(action.clone()),
            };
```

**File:** docs/architecture/how/meta-tx.md (L54-59)
```markdown
## Relayer

Meta transactions only work with a relayer. This is an application layer
concept, implemented off-chain. Think of it as a server that accepts a
`SignedDelegateAction`, does some checks on them and eventually forwards it
inside a transaction to the blockchain network.
```
