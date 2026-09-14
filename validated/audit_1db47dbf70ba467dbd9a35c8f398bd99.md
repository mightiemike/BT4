## Title
`DelegateAction`/`DelegateActionV2` signed payload omits chain/genesis binding, enabling meta-transaction replay across NEAR-protocol networks that share account/key state - ([File: core/primitives/src/action/delegate.rs])

### Summary
The Sherlock report flags that `getTransactionHash` omits `chainId`, so identical transactions on different chains hash the same and can be confused with each other. The nearcore analog is `DelegateAction`/`DelegateActionV2`, the payload a user signs for NEP-366 meta-transactions. Unlike `Transaction`/`TransactionV1`, which bind to a specific chain via `block_hash` (checked by `chain_validate` against the current chain's recent blocks), `DelegateAction` has no `block_hash`, genesis hash, or chain identifier at all in its signed fields.

### Finding Description
`DelegateAction` (`core/primitives/src/action/delegate.rs:46-64`) and `DelegateActionV2` (`:119-133`) only contain `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, `public_key`. The signature is computed over `get_nep461_hash()`, which just NEP-461-tags and borsh-serializes this struct (`:349-357`, `:176-184`) - no chain-specific salt is included. [1](#0-0) [2](#0-1) 

Contrast this with ordinary `Transaction`s, which embed `block_hash` (`core/primitives/src/transaction.rs:199-204`) and are checked against the live chain via `chain_validate` at admission time (`chain/chain/src/runtime/mod.rs:1061-1066`), giving them implicit chain-uniqueness because different chains have disjoint block hashes. [3](#0-2) [4](#0-3) 

`DelegateAction` has no equivalent field. Its only replay protection is the access-key/gas-key nonce, validated in `validate_delegate_action_key`/`apply_delegate_action` in `runtime/runtime/src/actions.rs:453-497`, which checks the nonce against the on-chain access key state of `sender_id`/`public_key` and `max_block_height` against the local block height - both purely local, chain-agnostic values. [5](#0-4) 

Because nothing in the signed bytes ties a `SignedDelegateAction` to a specific chain/genesis, the exact same signed bytes are valid on any NEAR-protocol-compatible network where the signer's account exists with the same public key and the same (or lower) access-key nonce - e.g., a testnet that shares genesis/account state with mainnet, a forked/duplicated chain, or any other deployment carrying the same account and key. This mirrors the reported bug class exactly: the signed "transaction" hash/identity has no chain-domain separator, so the same signed payload can be legitimately accepted on two different chains for two different intents.

### Impact Explanation
If a relayer or wallet infrastructure operates across multiple NEAR-protocol chains that can end up with overlapping account/access-key state (e.g., a test network cloned from mainnet state, a hard-forked chain, or any environment reusing account data), a `SignedDelegateAction` signed by a user for one chain can be relayed and executed unmodified on another chain, performing unauthorized actions (fund transfers via `FunctionCall`/`Transfer` actions bundled in the delegate action) on that second chain against the user's wishes. This is unauthorized value movement executed via a signature the user never intended to authorize for that chain, satisfying the "concrete unauthorized value movement" acceptance bar.

### Likelihood Explanation
Exploitation requires the two chains to share overlapping account state (same `sender_id`, same `public_key`, same or lower stored nonce) — a real-world scenario in NEAR-protocol chain forks, testnets seeded from mainnet snapshots, or any interoperating network sharing genesis/account data with mainnet, since nothing in `DelegateAction` prevents cross-chain replay by design (unlike top-level transactions, which are implicitly bound via `block_hash`/`chain_validate`).

### Recommendation
Include a chain-specific domain separator in the NEP-461 signed payload for `DelegateAction`/`DelegateActionV2` — e.g., mix in the genesis hash or a protocol-configured chain id into `get_nep461_hash()` (similar to how `SignableMessage`/`SignableMessageType` already tags the message type) so that a signed delegate action is cryptographically bound to one specific chain and cannot be replayed on another chain even when account/key state coincidentally matches.

### Proof of Concept
1. Chain A (e.g., mainnet) and Chain B (e.g., a testnet or fork sharing genesis/account state) both have account `alice.near` with the same public key and access-key nonce `N`.
2. Alice signs a `SignedDelegateAction` (via `SignedDelegateAction::sign`/`VersionedSignedDelegateAction::sign`, `core/primitives/src/action/delegate.rs:92-95`, `:216-219`) authorizing a transfer/function call on Chain A, with `nonce = N+1` and a generous `max_block_height`. [6](#0-5) 
3. A relayer wraps it in `Action::Delegate`/`Action::DelegateV2` inside a `SignedTransaction` and submits it to Chain B instead. `apply_delegate_action` on Chain B verifies the signature (valid, since the hash has no chain binding), checks `max_block_height` against Chain B's local height (passes), and checks the nonce against Chain B's stored access-key nonce for `alice.near`/public key (passes, since state coincidentally matches), then executes the inner actions on Chain B. [7](#0-6) 
4. The delegate action executes on the unintended chain, moving funds/performing actions Alice never authorized there.

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

**File:** core/primitives/src/action/delegate.rs (L92-95)
```rust
    pub fn sign(singer: &Signer, delegate_action: DelegateAction) -> Self {
        let signature = singer.sign(delegate_action.get_nep461_hash().as_bytes());
        Self { delegate_action, signature }
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

**File:** core/primitives/src/transaction.rs (L199-204)
```rust
    pub fn block_hash(&self) -> &CryptoHash {
        match self {
            Transaction::V0(tx) => &tx.block_hash,
            Transaction::V1(tx) => &tx.block_hash,
        }
    }
```

**File:** chain/chain/src/runtime/mod.rs (L1061-1066)
```rust
                // Verifying the transaction is on the same chain and hasn't expired yet.
                if !chain_validate(&validated_tx.to_signed_tx()) {
                    tracing::trace!(target: "runtime", tx=?validated_tx.get_hash(), "discarding transaction that failed chain validation");
                    rejected_invalid_for_chain += 1;
                    continue;
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
