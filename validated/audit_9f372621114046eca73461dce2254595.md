### Title
Meta-transaction (`DelegateAction`/`DelegateActionV2`) signatures omit chain/genesis identifier, enabling cross-fork replay - (File: `core/primitives/src/signable_message.rs`, `core/primitives/src/action/delegate.rs`)

### Summary
NEAR's meta-transaction scheme (NEP-366/NEP-611) signs a `DelegateAction`/`DelegateActionV2` using `SignableMessage`, whose signed payload consists only of a fixed `MessageDiscriminant` plus the action body (`sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, `public_key`). No chain identifier (chain ID, genesis hash, or genesis-derived value) is included anywhere in the signed bytes, unlike the cached `DOMAIN_SEPARATOR` scenario in the referenced report where a fixed chain-bound value is reused after a fork. If the NEAR chain undergoes a fork that produces two independently live chains sharing pre-fork history (and therefore identical account/access-key state at the fork point), a previously signed `SignedDelegateAction`/`VersionedSignedDelegateAction` remains fully valid and replayable on both branches.

### Finding Description
The signature that authorizes a delegate action is computed as: [1](#0-0) 

`SignableMessage` combines only a `MessageDiscriminant` (a static NEP-derived tag) and the message body via `borsh::to_vec` before hashing and signing — there is no chain-specific salt: [2](#0-1) 

The `DelegateAction` body itself carries no chain-binding field either — only `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key`: [3](#0-2) 

and the hash actually signed is produced purely from this struct plus the discriminant: [4](#0-3) 

The only replay defenses are the access-key `nonce` and `max_block_height`, both validated against on-trie state at apply time: [5](#0-4) 

Crucially, `nonce` and `max_block_height` are relative, chain-agnostic values (an integer counter and a block height number), not a chain- or genesis-bound hash. In case of a chain fork where two branches share identical pre-fork history, both branches start with the same access-key nonce and the same block-height numbering scheme, so a `SignedDelegateAction` that validates on one branch will validate on the other as long as its `max_block_height` has not been exceeded there and its `nonce` has not yet been consumed on that branch. Because the relayer (the outer `SignedTransaction` signer/predecessor) is not part of the signed `DelegateAction` payload, any party who has observed a broadcast `SignedDelegateAction` (e.g., from mempool gossip or an included block on one branch) can re-wrap the same bytes as their own relayer transaction and submit it to the other branch, exactly mirroring the reported "cached domain separator" replay pattern but with an entirely absent chain identifier rather than a stale cached one.

### Impact Explanation
A signed meta-transaction (which can move funds, call contracts, add/delete keys, etc., on behalf of `sender_id`) can be replayed on both sides of a chain fork by any observer, without further cooperation from the original signer. This causes unauthorized duplicate execution of the sender's authorized actions (e.g., duplicate transfers, duplicate contract calls) on a branch the sender never intended to interact with, i.e., unauthorized value movement / invalid state transition acceptance stemming purely from missing domain separation in the signature scheme.

### Likelihood Explanation
This requires a chain fork event (e.g., a contentious hard fork or any scenario producing two live chains sharing pre-fork history/state) — a low-frequency but realistic event for any blockchain, and one the referenced report explicitly treats as the trigger condition. Once such a fork occurs, exploitation requires no privileged access: any party holding a previously broadcast `SignedDelegateAction` can relay it to the sibling chain using only their own account as the outer transaction's `signer_id`/fee payer.

### Recommendation
Include a chain-binding value (e.g., genesis hash or a network/chain identifier) inside the `SignableMessage`/`DelegateAction`(`V2`) payload that gets hashed and signed, similar to how EIP-155/EIP-712 domain separators bind a chain ID into the signed digest. This ensures a `SignedDelegateAction` produced for one chain cannot be validated against the sibling chain state after a fork.

### Proof of Concept
1. User signs a `DelegateAction` (via `SignedDelegateAction::sign`) authorizing a token transfer, with `nonce = N`, `max_block_height = H+100` at chain height `H`. [6](#0-5) 
2. A relayer submits it in a `SignedTransaction` on chain-A; it is included in a block, incrementing the access key nonce on chain-A only.
3. Suppose, at height `H`, the network forks into chain-A and chain-B, each continuing independently but sharing all state up to `H` (including the sender's un-incremented access key nonce and account balance, since the fork happened before/at the point the relayer's tx was included on one branch but not the other, or more generally any observer captured the raw bytes before divergence).
4. Any third party who has the raw `SignedDelegateAction` bytes (visible in the included block or mempool) constructs a new outer `SignedTransaction` using their own relayer key and submits the exact same `SignedDelegateAction` to chain-B.
5. On chain-B, `validate_delegate_action_key` sees nonce `N` still unspent and `max_block_height = H+100` still in the future for chain-B's height, so the signature and freshness checks pass: [7](#0-6) 
6. The transfer/action is executed a second time on chain-B without any further authorization from the original sender — proving replay across the fork due to the absence of a chain-binding value in the signed payload.

### Citations

**File:** core/primitives/src/signable_message.rs (L61-65)
```rust
#[derive(BorshSerialize)]
pub struct SignableMessage<'a, T> {
    pub discriminant: MessageDiscriminant,
    pub msg: &'a T,
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

**File:** core/primitives/src/action/delegate.rs (L344-358)
```rust
impl DelegateAction {
    pub fn get_actions(&self) -> Vec<Action> {
        self.actions.iter().map(|a| a.clone().into()).collect()
    }

    /// Delegate action hash used for NEP-461 signature scheme which tags
    /// different messages before hashing
    ///
    /// For more details, see: [NEP-461](https://github.com/near/NEPs/pull/461)
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
        let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
        hash(&bytes)
    }
}
```

**File:** runtime/runtime/src/actions.rs (L1583-1621)
```rust
    #[test]
    fn test_validate_delegate_action_key_update_nonce() {
        let (_, signed_delegate_action) = create_delegate_action_receipt();
        let sender_id = &signed_delegate_action.delegate_action.sender_id;
        let sender_pub_key = &signed_delegate_action.delegate_action.public_key;
        let access_key = AccessKey { nonce: 19000000, permission: AccessKeyPermission::FullAccess };

        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height);
        let mut state_update = setup_account(sender_id, sender_pub_key, &access_key);

        // Everything is ok
        let mut result = ActionResult::default();
        validate_delegate_action_key(
            &mut state_update,
            &apply_state,
            (&signed_delegate_action.delegate_action).into(),
            &mut result,
        )
        .expect("Expect ok");
        assert!(result.result.is_ok(), "Result error: {:?}", result.result);

        // Must fail, Nonce had been updated by previous step.
        result = ActionResult::default();
        validate_delegate_action_key(
            &mut state_update,
            &apply_state,
            (&signed_delegate_action.delegate_action).into(),
            &mut result,
        )
        .expect("Expect ok");
        assert_eq!(
            result.result,
            Err(ActionErrorKind::DelegateActionInvalidNonce {
                delegate_nonce: signed_delegate_action.delegate_action.nonce,
                ak_nonce: signed_delegate_action.delegate_action.nonce,
            }
            .into())
        );
```
