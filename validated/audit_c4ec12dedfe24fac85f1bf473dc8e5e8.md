## Analog Found: Meta-transaction (`DelegateAction`) signatures lack chain/network binding, enabling cross-chain/fork replay

### Title
Missing chain/network identifier in `SignedDelegateAction`/`VersionedSignedDelegateAction` signing scheme enables replay across NEAR networks or forks - (File: `core/primitives/src/signable_message.rs`, `core/primitives/src/action/delegate.rs`)

### Summary
The NEP-366 meta-transaction signature scheme signs a `DelegateAction`/`DelegateActionV2` under a `SignableMessage` that only mixes in a fixed protocol-defined discriminant (NEP number), `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key`. There is no genesis hash, chain id, or any other network-specific value included in the signed payload, unlike ordinary `SignedTransaction`s which are bound to a specific `block_hash` from the chain they were submitted to.

### Finding Description
`SignedDelegateAction::verify` and `VersionedSignedDelegateAction::verify` compute the signed hash via `get_nep461_hash`, which wraps the action in a `SignableMessage` and hashes it: [1](#0-0) [2](#0-1) 

The `SignableMessage` struct itself only contains a `discriminant` (a static per-NEP constant) and the message body: [3](#0-2) [4](#0-3) 

None of these fields are unique to a particular NEAR network instance (mainnet, testnet, or any nearcore-derived chain that shares the same genesis/account namespace, e.g. a copied/forked chain or a new network bootstrapped from a mainnet state snapshot). The only replay defenses are:
- `nonce`, checked against the access key's on-chain nonce in `validate_delegate_action_key`, and
- `max_block_height`, checked against the local `apply_state.block_height`. [5](#0-4) [6](#0-5) 

Both of these are local, per-chain, mutable counters — they do not encode which network the signer intended the message for. If the same account id and public key exist on two different NEAR-protocol chains (e.g. a testnet reset from mainnet state, a permissioned/enterprise fork of nearcore, or any new network instantiated by copying genesis state with the same accounts/keys), a captured `SignedDelegateAction` valid on chain A will also verify on chain B as long as the nonce/height constraints happen to hold there too, since the signature check has no way to distinguish the two chains.

This differs materially from ordinary transactions, which include a recent `block_hash` from the specific chain's block history in `SignedTransaction`, making them practically impossible to replay across distinct chains (different chains diverge in block hashes almost immediately). `DelegateAction` deliberately omits this binding (it uses `max_block_height`/`nonce` instead per NEP-366 design), and no substitute chain-scoping value was added.

### Impact Explanation
An attacker or relayer who obtains a valid `SignedDelegateAction` (which is inherently meant to be handed off to a semi-trusted relayer per the meta-transaction flow described in `docs/architecture/how/meta-tx.md`) can wrap and resubmit that exact same signed payload on any other NEAR-protocol chain that shares the sender's account id, public key, and has not yet advanced the corresponding nonce past the signed value. This can result in unauthorized execution of the inner actions (e.g. token transfers, `AddKey`, contract calls) against the sender's account on that other chain, without the sender ever intending to authorize action on that chain — a form of unauthorized value movement / unauthorized state transition.

### Likelihood Explanation
Exploitability depends on there being two live NEAR-protocol networks/forks that share overlapping account+key state (e.g. testnet snapshots seeded from mainnet, migration test networks, or third-party forks of nearcore reusing mainnet genesis data) — a realistic and recurring situation in this ecosystem (testnets are frequently reset from mainnet state dumps). Any relayer or intermediary who receives a `SignedDelegateAction` off-chain (by design, per NEP-366) can attempt to replay it elsewhere; the runtime provides no mechanism to reject it based on chain identity.

### Recommendation
Bind the meta-transaction signature to the specific chain instance, e.g. by including the chain's genesis hash (or another canonical, network-unique identifier) as an additional field inside `DelegateAction`/`DelegateActionV2`, and validate it against the local `apply_state`'s genesis hash in `apply_delegate_action`/`validate_delegate_action_key`, analogous to how a genesis hash is used to disambiguate other on-chain identifiers.

### Proof of Concept
1. Instantiate chain A (e.g. mainnet) and chain B (e.g. a testnet/fork) such that account `alice.near` exists on both with the same public key and access-key nonce state (a realistic scenario when a testnet is bootstrapped from a mainnet state export).
2. Alice signs a `DelegateAction{sender_id: "alice.near", receiver_id: "ft.near", actions: [transfer], nonce: N, max_block_height: H, public_key: pk}` and hands it to relayer R on chain A, per the flow in `integration-tests/src/user/mod.rs` `meta_tx` / `test-loop-tests/src/tests/meta_tx.rs` `test_meta_tx`.
3. Relayer R (or anyone who intercepts the `SignedDelegateAction`) also wraps the identical bytes into a transaction on chain B, addressed to `alice.near`'s counterpart account there, before `nonce` N is consumed and while `block_height < H` on chain B.
4. `apply_delegate_action`/`validate_delegate_action_key` on chain B accept it because `signed_delegate_action.verify()` succeeds (the hash contains no chain-specific data) and nonce/height checks pass independently on chain B, executing Alice's inner actions on chain B without her consent for that chain. [7](#0-6)

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

**File:** core/primitives/src/signable_message.rs (L61-65)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
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

**File:** runtime/runtime/src/actions.rs (L453-482)
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
```

**File:** runtime/runtime/src/actions.rs (L605-666)
```rust
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
