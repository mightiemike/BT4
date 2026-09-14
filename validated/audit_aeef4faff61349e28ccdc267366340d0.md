## Title
Meta-transaction (`DelegateAction`/NEP-366) signatures lack chain/network binding, enabling cross-fork replay of relayed transactions - (File: `core/primitives/src/action/delegate.rs`)

### Summary
NEAR's native meta-transaction mechanism (`DelegateAction`, NEP-366/NEP-611) signs only `sender_id, receiver_id, actions, nonce, max_block_height, public_key` via the NEP-461 tagging scheme, with no chain/network identifier and no `block_hash` binding. Ordinary `SignedTransaction`s are protected from cross-fork/cross-network replay by embedding a recent `block_hash`, which ties the signature to a specific chain history. `DelegateAction`, however, is only bound to an access-key `nonce` and an absolute `max_block_height`, neither of which is chain-specific. This is structurally the same class of bug as the reported ERC20Permit missing-`chainID` issue: a signed authorization message that is valid, unmodified, on more than one chain that shares state at the point of divergence.

### Finding Description
`SignedDelegateAction::verify` / `VersionedSignedDelegateAction::verify` compute a signature hash purely from the `DelegateAction`/`DelegateActionV2` payload and a NEP-461 message discriminant: [1](#0-0) [2](#0-1) 

The discriminant only tags the NEP number/message type, never a chain or network identifier: [3](#0-2) 

Compare this with regular transactions, which are explicitly documented as being bound to a specific chain via `block_hash`: [4](#0-3) 

The `tools/mirror` documentation independently confirms that `block_hash` is precisely what prevents transactions from one chain being replayed on a forked/sibling chain, forcing operators to re-key accounts when mirroring state across chains: [5](#0-4) 

`DelegateAction` has no equivalent field. When the relayer's outer transaction is applied, `apply_delegate_action` only checks the inner signature, `max_block_height` (an absolute height, not a hash), and sender/receiver/access-key/nonce state — none of which reference the specific chain instance: [6](#0-5) [7](#0-6) 

The relevant NEP-366 doc explicitly states nonce/access-key state is the only replay defense contemplated for meta-transactions: [8](#0-7) 

Because the `DelegateAction` bytes that get signed do not commit to any chain-identifying value, the same signed payload remains valid on any chain (a contentious fork, a state-forked replica such as those created for `mirror`/testing, or any two networks that happen to share the sender's account, access key, and nonce state) as long as the relayer wraps it in a *new* outer `SignedTransaction` with a `block_hash` valid on that particular chain. The outer transaction's `block_hash` freshness check protects the relayer's own tx, but says nothing about the chain-validity of the inner `DelegateAction` signature it carries.

By contrast, the `near-wallet-contract` (used for ETH-implicit accounts / RLP-encoded Ethereum-style transactions) explicitly validates the `chain_id` field of the emulated Ethereum transaction and bans relayers who submit signatures for the wrong chain, showing the project is aware of and mitigates this exact bug class elsewhere, but not for native NEP-366 meta-transactions: [9](#0-8) 

### Impact Explanation
Any user who signs a `DelegateAction` (e.g., authorizing a transfer, a function call, an `AddKey`, etc.) for execution via a relayer implicitly authorizes that exact action to be replayed on **any** chain where their account, public key, and access-key nonce still validate — for example immediately after a contentious hard fork that splits mainnet/testnet-like state, or on any state-forked replica sharing the pre-fork account state (which nearcore's own tooling, `tools/mirror`, demonstrates is a realistic operational scenario for NEAR-based networks). A relayer (which need not be trusted or even the same relayer the user dealt with) can capture the `SignedDelegateAction` from one chain and resubmit it, wrapped in a fresh outer transaction, on the sibling chain, executing the user's authorized action (fund transfer, permission grant, contract call) a second time without the user's consent. This is unauthorized value movement / unauthorized state transition acceptance triggered purely by resubmitting an already-signed, unprivileged artifact.

### Likelihood Explanation
Exploitation requires only an unprivileged transaction submitter (a relayer, which by design in NEP-366 is untrusted from the signer's perspective) and access to a previously observed `SignedDelegateAction`, plus the existence of two chains sharing the signer's account/access-key state (post-fork chains, or state-forked test/shadow networks such as those `mirror` and similar forking tools produce). No validator collusion, no network-layer attack, and no operator-only capability is needed — it is reachable from ordinary transaction submission and relayer behavior.

### Recommendation
Bind the `DelegateAction` signature payload to the specific chain instance, analogous to how `SignedTransaction` uses `block_hash`. Concretely, include a `block_hash` (or a genesis/chain identifier) field inside `DelegateAction`/`DelegateActionV2` and require `apply_delegate_action`/`validate_delegate_action_key` to check it against the executing chain's recent block hashes (mirroring `transaction_validity_period` handling for ordinary transactions), so a captured `SignedDelegateAction` cannot be replayed on a sibling or forked chain that shares pre-fork account state.

### Proof of Concept
1. Alice signs `DelegateAction { sender_id: alice, receiver_id: bob, actions: [Transfer{amount}], nonce: N, max_block_height: H, public_key: alice_pk }` for a relayer, per [10](#0-9) , intending it to execute once on chain A.
2. Chain A experiences a fork (or a state-forked sibling chain/testnet exists) at height < H, so chain B shares Alice's account, `alice_pk` access key with nonce < N, and is still below block height H.
3. A relayer (any account, not necessarily the one Alice dealt with) wraps the exact same `SignedDelegateAction` bytes inside a brand new outer `SignedTransaction` with a `block_hash` that is valid/fresh on chain B, per the `block_hash`-only freshness check described in [4](#0-3) .
4. `apply_delegate_action` on chain B verifies the inner signature successfully (identical bytes, identical `alice_pk`), passes the `max_block_height` check (chain B height < H), and passes the nonce check (chain B's access key nonce is still < N), per [11](#0-10)  and [12](#0-11) .
5. The transfer to Bob executes a second time on chain B, moving Alice's funds without a second authorization — exactly the "signed message valid on both forks" scenario described in the original ERC20Permit report.

### Citations

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

**File:** docs/RuntimeSpec/Scenarios/FinancialTransaction.md (L53-63)
```markdown
The block hash is used to calculate a transaction's "freshness".  It
is used to make sure a transaction does not get lost (let's say
somewhere in the network) and then arrive days, weeks, or years later
when it is not longer relevant and would be undesirable to execute.
A transaction does not need to arrive at a specific block, instead
it is required to arrive within a certain number of blocks from the block
identified by the `block_hash`.  Any transaction arriving outside this
threshold is considered to be invalid.  Allowed delay is defined by
`transaction_validity_period` option in the chain’s genesis file.  On
mainnet, this value is 86400 (which corresponds to roughly a day) and
on testnet it is 100.
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

**File:** runtime/runtime/src/actions.rs (L453-490)
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
```

**File:** runtime/runtime/src/actions.rs (L574-616)
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
```

**File:** docs/architecture/how/meta-tx.md (L127-137)
```markdown
## Limitation: Accounts must be initialized

Any transaction, including meta transactions, must use NONCEs to avoid replay
attacks. The NONCE must be chosen by Alice and compared to a NONCE stored on
chain. This NONCE is stored on the access key information that gets initialized
when creating an account.

Implicit accounts don't need to be initialized in order to receive NEAR tokens,
or even $FT. This means users could own $FT but no NONCE is stored on chain for
them. This is problematic because we want to enable this exact use case with
meta transactions, but we have no NONCE to create a meta transaction.
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/relayer.rs (L242-276)
```rust
// A relayer sending a transaction signed with the wrong chain id is a ban-worthy offense.
#[tokio::test]
async fn test_relayer_wrong_chain_id() -> anyhow::Result<()> {
    let TestContext { worker, mut wallet_contract, wallet_sk, wallet_address, .. } =
        TestContext::new().await?;

    let relayer_pk = wallet_contract.register_relayer(&worker).await?;

    let transaction = aurora_engine_transactions::eip_2930::Transaction2930 {
        nonce: 0.into(),
        gas_price: 0.into(),
        gas_limit: 0.into(),
        to: Some(Address::new(wallet_address)),
        value: Wei::zero(),
        data: [
            crate::eth_emulation::ERC20_BALANCE_OF_SELECTOR.to_vec(),
            ethabi::encode(&[ethabi::Token::Address(wallet_address)]),
        ]
        .concat(),
        chain_id: CHAIN_ID + 1,
        access_list: Vec::new(),
    };
    let signed_transaction = crypto::sign_transaction(transaction, &wallet_sk);

    let result = wallet_contract
        .rlp_execute(wallet_contract.inner.id().as_str(), &signed_transaction)
        .await?;

    assert!(!result.success);
    assert_eq!(result.error.as_deref(), Some("Error: faulty relayer"));

    assert_revoked_key(&wallet_contract.inner, &relayer_pk).await;

    Ok(())
}
```
