Confirmed: `DelegateAction`'s signed payload contains only `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, `public_key` — no `chain_id` or `genesis_hash`. This confirms the analog is valid.### Title
Meta-transaction (`DelegateAction` / `DelegateActionV2`) signatures omit chain/genesis binding, enabling cross-fork replay of relayer-submitted actions - (File: `core/primitives/src/action/delegate.rs`, `core/primitives/src/signable_message.rs`)

### Summary
NEAR's meta-transaction scheme (NEP-366/NEP-611) has the sender sign a `DelegateAction`/`DelegateActionV2` payload that is later wrapped and submitted on-chain by an untrusted relayer. The signed payload only binds to `sender_id`, `receiver_id`, `actions`, `nonce`/`TransactionNonce`, `max_block_height`, and `public_key` — it contains no `chain_id` or `genesis_hash`. This is structurally the same root cause as the referenced C4 finding: a signed authorization message lacking a chain-domain-separator (the NEAR analog of EIP-712's `chainId`/`verifyingContract`), combined with the fact that "anyone" (any relayer) can submit the signed message on-chain to trigger fund movement or privileged actions.

### Finding Description
`SignedDelegateAction::sign`/`verify` and `VersionedSignedDelegateAction` hash exactly the `DelegateAction`/`DelegateActionV2` struct plus a `MessageDiscriminant` (NEP number), via `get_nep461_hash()`: [1](#0-0) 

The struct being signed carries no notion of which NEAR network/chain instance it is valid on: [2](#0-1) [3](#0-2) 

The `SignableMessage` wrapper that produces the signed hash only adds a NEP discriminant, not a chain identifier: [4](#0-3) 

Unlike an ordinary `SignedTransaction`, which at least references a specific recent `block_hash` (weak, time-bounded chain-state binding), the delegate-action replay protection relies solely on an access-key `nonce` (or the gas-key `TransactionNonce` index) plus a `max_block_height` ceiling — both of which are pure local counters with no reference to any specific chain's identity or genesis. Contrast this with the NEAR Wallet Contract's EVM-compatible relayer path, which explicitly validates `chain_id` and revokes the relayer key on mismatch, confirming that chain-binding is understood as a necessary security control elsewhere in the codebase but is absent from the native meta-transaction signing scheme: [5](#0-4) 

If two live NEAR-protocol networks ever share the same account/access-key/nonce state at a given point (e.g., a contentious protocol-level fork/chain-split producing two chains that inherit identical state from a common ancestor, or any deliberately-launched network that clones mainnet state), a `SignedDelegateAction` produced by a user for submission on one network is fully valid to submit verbatim by a relayer on the other network, because nothing in the signed bytes ties it to a specific genesis/chain. The relayer-submission path itself is deliberately open to "anyone," as documented for meta-transactions generally: [6](#0-5) 

Nonce-based replay protection is enforced per network's own state at execution time in `runtime/runtime/src/actions.rs`: [7](#0-6) 

This only prevents replay *within a single network's state history* — it does nothing to prevent the identical signed bytes from being valid and accepted on a second network whose access-key nonce state has not yet diverged from the first.

### Impact Explanation
On a network split (fork), any relayer (an unprivileged, permissionless role — anyone can act as a relayer for a `DelegateAction`) can capture a `SignedDelegateAction` observed on chain A and resubmit the identical bytes on chain B before Alice's nonce advances there. This causes the exact authorized action (e.g., a token transfer, `AddKey`, or function call) to execute a second time on the sibling chain with the same funds/state effect the sender only intended to authorize once, i.e. unauthorized/duplicated value movement and/or unauthorized execution of a privileged action (e.g. adding an access key) on a chain the sender never intended to interact with. This matches "concrete unauthorized value movement" / "invalid state transition acceptance" criteria, reachable purely from a submitted signed message plus a permissionless relayer transaction — no validator, network, or operator privilege required.

### Likelihood Explanation
Likelihood is conditioned on an actual NEAR network split/fork event (contentious protocol upgrade, state-cloned test/shadow network, or similar) occurring while the two networks' access-key nonce state for the targeted account has not yet diverged. This is a lower-frequency but historically real class of event for blockchains generally (the C4 report's own motivating scenario), and unlike ordinary transactions (bound to a specific recent `block_hash`), delegate actions have *no* chain-specific binding at all, making them strictly more replayable than regular transactions across a fork. Given NEAR's own wallet-contract code explicitly guards against `chain_id` mismatch for EVM-style transactions, but the native `DelegateAction`/`DelegateActionV2` path has no equivalent check, this is a genuine design gap rather than a hypothetical.

### Recommendation
Add a network/chain-domain-separator field (e.g. `genesis_hash` or `chain_id`) into the signed payload of `DelegateAction`/`DelegateActionV2` (and cover it in `get_nep461_hash()`/`SignableMessage`), and validate it against the receiving chain's genesis at the same point `DelegateActionInvalidNonce`/`DelegateActionExpired` are currently checked in `runtime/runtime/src/actions.rs`. This mirrors EIP-712's `chainId` domain separator recommended in the original finding and is consistent with the pattern already used for wallet-contract relayer validation.

### Proof of Concept
1. Network N1 (e.g. mainnet) and network N2 come to share identical account state (a fork/split scenario, or a state-cloned network) at some height H, including Alice's access key and its current `nonce`.
2. Alice signs a `SignedDelegateAction` (transfer of funds to Bob, or `AddKey`) using `SignedDelegateAction::sign` / `VersionedSignedDelegateAction::sign`, intending it for submission via a relayer only on N1. [8](#0-7) 
3. A relayer submits it on N1; it executes and Alice's nonce advances on N1.
4. Before Alice's nonce advances on N2 (which still has the pre-fork nonce value), an attacker who observed the `SignedDelegateAction` bytes on N1 submits the identical bytes via a relayer transaction on N2.
5. `SignedDelegateAction::verify()` succeeds on N2 because the signature check only validates the hash of `sender_id`/`receiver_id`/`actions`/`nonce`/`max_block_height`/`public_key` — none of which differ between N1 and N2 — and the nonce check in `runtime/runtime/src/actions.rs` passes because N2's on-chain nonce for that key still matches the pre-fork value. [9](#0-8) 
6. The delegated action (fund transfer / key addition) executes a second time on N2, which Alice never authorized for that network.

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

**File:** core/primitives/src/signable_message.rs (L61-107)
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

**File:** docs/architecture/how/meta-tx.md (L40-53)
```markdown
With meta transactions, Alice can create a `DelegateAction`, which is very
similar to a transaction. It also contains a list of actions to execute and a
single receiver for those actions. She signs the `DelegateAction` and forwards
it (off-chain) to a relayer. The relayer wraps it in a transaction, of which the
relayer is the signer and therefore pays the gas costs. If the inner actions
have an attached token balance, this is also paid for by the relayer.

On chain, the `SignedDelegateAction` inside the transaction is converted to an
action receipt with the same `SignedDelegateAction` on the relayer's shard. The
receipt is forwarded to the account from `Alice`, which will unpacked the
`SignedDelegateAction` and verify that it is signed by Alice with a valid Nonce
etc. If all checks are successful, a new action receipt with the inner actions
as body is sent to `FT`. There, the `ft_transfer` call finally executes.

```

**File:** runtime/runtime/src/actions.rs (L648-666)
```rust
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
