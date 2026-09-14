This confirms the analog exists. Key facts:

- `DelegateAction` / `DelegateActionV2` signatures are computed via `SignableMessage::new(&delegate_action, SignableMessageType::DelegateAction/...)` and hashed with NEP-461 tagging [1](#0-0) . The signed payload contains only `sender_id, receiver_id, actions, nonce, max_block_height, public_key` — no genesis hash, chain ID, or network identifier [2](#0-1) .
- NEAR-implicit account IDs are the hex encoding of the ED25519 public key itself, deterministic and independent of any network [3](#0-2) , so the same `account_id` and `public_key` exist identically on every nearcore-based network (mainnet, testnet, or any independently deployed nearcore chain) for a given key pair.
- The access key nonce for a freshly created implicit account is seeded deterministically from `initial_nonce_value(block_height)` [4](#0-3) , so two networks with similar block heights at account-creation time (or the more general case of nonce collisions after normal usage) can produce/accept the identical nonce state.
- The outer `Transaction`/`SignedTransaction` wrapping a delegate action does carry a `block_hash` for freshness checks [5](#0-4) , but that binds only the *relayer's* outer envelope, not the *inner* `SignedDelegateAction`. Any relayer can repackage a captured `SignedDelegateAction` into a brand-new, validly-fresh outer transaction on a different chain, and `validate_delegate_action_key` only checks nonce/permissions against local state, with no chain-binding check [6](#0-5) .

### Title
Cross-chain/cross-network replay of `SignedDelegateAction` (NEP-366 meta-transactions) due to missing chain/network identifier in the NEP-461 signed hash - (File: core/primitives/src/action/delegate.rs)

### Summary
A `SignedDelegateAction`'s signature is computed over `{sender_id, receiver_id, actions, nonce, max_block_height, public_key}` tagged only with an on-chain NEP discriminant (NEP-366/NEP-611), never a chain ID, genesis hash, or network identifier. Because nearcore is deployed as multiple independent networks that share the same protocol/binary (mainnet, testnet, and any other nearcore-based chain), and NEAR/ETH-implicit account IDs are derived purely from the public key (identical across all such networks), a `SignedDelegateAction` captured from one network can be repackaged by any relayer into a fresh outer transaction and replayed on another network where the same account/key/nonce state coincides.

### Finding Description
`DelegateAction::get_nep461_hash` / `VersionedDelegateActionPayload::get_nep461_hash` build the signed hash from a `SignableMessage` wrapping only the delegate action fields plus a fixed NEP-based discriminant, with no per-network salt [7](#0-6) [8](#0-7) . Verification (`SignedDelegateAction::verify` / `VersionedSignedDelegateAction::verify`) only checks the signature against this hash and the embedded `public_key` [9](#0-8) [10](#0-9) .

On the runtime side, `validate_delegate_action_key` authorizes the delegate action purely from local, per-chain state: it looks up the access key for `sender_id`/`public_key`, checks/advances the nonce, and checks permissions — it has no notion of "this action was intended for network X" [6](#0-5) .

By design, a `SignedDelegateAction` is meant to be wrapped by *any* relayer into a fresh outer `Transaction`/`SignedTransaction`, whose own `block_hash`/nonce only protect the relayer's own transaction, not the inner delegated authorization (`docs/architecture/how/meta-tx.md`) [11](#0-10) . Since nearcore is the shared codebase for multiple deployed networks (mainnet, testnet, and independently operated nearcore-based chains), and NEAR-implicit/ETH-implicit account IDs are purely functions of the public key (`docs/DataStructures/Account.md:95-106`), the same `(sender_id, public_key)` pair — and potentially the same access-key nonce, since it is deterministically seeded from `block_height` at implicit-account creation (`actions.rs:234-239`) — can exist on two different networks. Nothing in the delegate-action signature scheme prevents a captured `SignedDelegateAction` intended for network A from being validly re-wrapped and executed on network B.

### Impact Explanation
If an attacker or a naive/malicious relayer that operates against multiple nearcore-based networks intercepts a user's `SignedDelegateAction` (e.g., from relayer logs, a public mempool/explorer, or a leaked relayer API request), they can replay the exact same delegated authorization on another network sharing the account's key/nonce state, causing unauthorized execution of the user's delegated actions (transfers, function calls, key management) without the user's consent on a network they never intended to transact on. This is unauthorized value movement / unauthorized state transition acceptance triggered purely by a transaction submission, matching the impact bar (concrete unauthorized value movement / invalid state transition acceptance).

### Likelihood Explanation
Exploitability depends on an attacker being able to observe a `SignedDelegateAction` (relayers necessarily see the full signed bytes to wrap them) and on the existence of matching account/key/nonce state on a second nearcore-based network — most plausible for implicit accounts (deterministic ID from key) shortly after creation, or in ecosystems where the same relayer/infrastructure serves multiple networks (mainnet/testnet or app-specific nearcore forks). This requires no protocol-level privilege — any relayer or observer with access to signed bytes and knowledge of the target account's state on a second network can attempt the replay, making it reachable purely from submitted transactions/RPC calls.

### Recommendation
Include a persistent, protocol-known chain/network identifier (e.g., the genesis hash or `chain_id`) as part of the NEP-461 signable payload for `DelegateAction`/`DelegateActionV2`, analogous to how EIP-712/EIP-155 bind Ethereum signatures to a chain ID. Concretely, extend `SignableMessage`/`DelegateAction::get_nep461_hash` to fold in `apply_state`'s `chain_id` (already available via `epoch_info_provider.chain_id()`, used elsewhere in `actions.rs`) so that a signature produced for one network's genesis cannot verify against another network's state, and update `validate_delegate_action_key` accordingly.

### Proof of Concept
1. Generate an ED25519 key pair; derive the NEAR-implicit `account_id` as the hex of the public key (`docs/DataStructures/Account.md:95-100`).
2. Fund/create this implicit account identically (same balance/state trajectory such that the seeded access-key nonce matches, per `initial_nonce_value(block_height)` at `runtime/runtime/src/actions.rs:236`) on two independently-run nearcore networks, A and B, that use the same protocol version.
3. On network A, sign a `DelegateAction` (e.g., transferring funds to `receiver_id`) using the key; obtain `SignedDelegateAction` bytes, e.g. via a relayer service.
4. Take the identical `SignedDelegateAction` bytes and wrap them inside a fresh `Transaction` (with a valid recent `block_hash` from network B) signed by any relayer account on network B; submit via `broadcast_tx_commit`.
5. Observe that `VersionedSignedDelegateAction::verify` succeeds (signature check has no chain binding) and `validate_delegate_action_key` accepts it (nonce matches network B's local state), executing the delegated action on network B even though the user only intended and authorized it for network A.

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

**File:** core/primitives/src/action/delegate.rs (L176-185)
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

**File:** docs/DataStructures/Account.md (L95-100)
```markdown
### NEAR-implicit account ID

The account ID is a lowercase hex representation of the public key.
An ED25519 public key is 32 bytes long and maps to a 64-character account ID.

Example: a public key in base58 `BGCCDDHfysuuVnaNVtEhhqeT4k9Muyem3Kpgq2U1m9HX` will map to the account ID `98793cd91a3f870fb126f66285808c7e094afcfc4eda8a970f6648cdf0dbd6de`.
```

**File:** runtime/runtime/src/actions.rs (L234-239)
```rust
        AccountType::NearImplicitAccount => {
            let mut access_key = AccessKey::full_access();
            access_key.nonce = initial_nonce_value(block_height);

            // unwrap: the arm we are in means `account_id` is 64 hex characters.
            let public_key = PublicKey::from_near_implicit_account(account_id).unwrap();
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

**File:** core/primitives/src/transaction.rs (L199-204)
```rust
    pub fn block_hash(&self) -> &CryptoHash {
        match self {
            Transaction::V0(tx) => &tx.block_hash,
            Transaction::V1(tx) => &tx.block_hash,
        }
    }
```

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

**File:** integration-tests/src/user/mod.rs (L283-318)
```rust
    /// Wrap the given actions in a delegate action and execute them.
    ///
    /// The signer signs the delegate action to be sent to the receiver. The
    /// relayer packs that in a transaction and signs it .
    fn meta_tx(
        &self,
        signer_id: AccountId,
        receiver_id: AccountId,
        relayer_id: AccountId,
        actions: Vec<Action>,
    ) -> Result<FinalExecutionOutcomeView, CommitError> {
        let inner_signer = create_user_test_signer(&signer_id);
        let user_nonce = self
            .get_access_key(&signer_id, &inner_signer.public_key())
            .expect("failed reading user's nonce for access key")
            .nonce;
        let delegate_action = DelegateAction {
            sender_id: signer_id.clone(),
            receiver_id,
            actions: actions
                .into_iter()
                .map(|action| NonDelegateAction::try_from(action).unwrap())
                .collect(),
            nonce: user_nonce + 1,
            max_block_height: 100,
            public_key: inner_signer.public_key(),
        };
        let signature = inner_signer.sign(delegate_action.get_nep461_hash().as_bytes());
        let signed_delegate_action = SignedDelegateAction { delegate_action, signature };

        self.sign_and_commit_actions(
            relayer_id,
            signer_id,
            vec![Action::Delegate(Box::new(signed_delegate_action))],
        )
    }
```
