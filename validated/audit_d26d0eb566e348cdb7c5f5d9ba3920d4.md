## Analog Found

### Title
Missing chain-binding data in `DelegateAction`/`DelegateActionV2` signed payload enables meta-transaction replay across forked/mirrored NEAR networks - (File: `core/primitives/src/action/delegate.rs`)

### Summary
The NEP-366 meta-transaction payload that a user signs off-chain, `DelegateAction`/`DelegateActionV2`, contains no chain-binding field analogous to Ethereum's `chainId` or NEAR's own regular-transaction `block_hash`. Its signed fields are limited to `sender_id`, `receiver_id`, `actions`, `nonce`, `max_block_height`, and `public_key` [1](#0-0) , and the signature is computed only over these fields via `get_nep461_hash()` [2](#0-1) . This mirrors the meebits `Offer` bug class: a signature that is valid on one instance of the protocol state remains valid and replayable on any other instance (fork/mirror) that shares the same account/access-key state, because nothing chain-specific is bound into what is signed.

### Finding Description
Ordinary NEAR transactions (`TransactionV0`/`TransactionV1`) include a `block_hash` field that binds the signature to a specific block on a specific chain history, and this is explicitly documented as the mechanism that prevents cross-chain/cross-fork replay [3](#0-2) . The `tools/mirror` utility, which forks mainnet state onto a separate "target chain" for testing, documents exactly this: byte-for-byte replay of source-chain transactions on the forked target chain fails because `block_hash` is chain-specific, forcing the tool to re-sign every outer transaction with a fresh target-chain `block_hash` [4](#0-3) .

However, `DelegateAction` (and `DelegateActionV2`) — the inner payload a user signs to authorize a relayer-submitted meta-transaction — has no such field. Its only replay defenses are:
- `nonce`, checked against the sender's access-key (or gas-key) nonce state [5](#0-4) , and
- `max_block_height`, a plain integer height with no chain identity attached [6](#0-5) .

Neither of these binds the signature to a particular chain's identity (genesis hash, chain id, or a specific block hash). Because a forked/mirrored network by construction starts with the *same* account and access-key nonce state as its source chain, a `SignedDelegateAction` that a user produced for use on chain A remains fully valid — same nonce, same `max_block_height` window, same public key — on any forked/mirrored chain B that has not yet advanced that account's nonce past the value in the delegate action. Unlike the outer `Transaction`, which must be re-signed per chain because of `block_hash`, the inner `DelegateAction` signature needs no modification at all to be replayed on the second chain: a relayer (an untrusted party by design in NEP-366, since anyone can wrap and submit a `SignedDelegateAction`) can simply take the previously-observed signed payload and submit it as a fresh outer transaction (with its own valid `block_hash`) against the forked network.

### Impact Explanation
Any user-signed meta-transaction (transfer, `AddKey` granting full access, staking action, etc.) captured by an observer can be re-submitted unmodified by any relayer on a second NEAR-protocol-compatible chain/fork that shares the signer's account and access-key nonce state (e.g., a mirrored/forked test network, a chain split, or any environment built from a state snapshot of the original chain before the nonce advanced there). This causes the user's authorized action to execute a second time on a chain the user never intended it for — unauthorized value movement (duplicate transfers), unauthorized key grants, or duplicate staking actions — without requiring any new signature or user consent. The relayer submitting the replay needs no elevated privilege: NEP-366 meta-transactions are explicitly designed so that "anyone" can act as relayer.

### Likelihood Explanation
Exploitability depends on the existence of a second chain/fork sharing the signer's account nonce state, which is a real, supported near workflow (`tools/mirror`, sandbox/test forks built from `dump-state`, or any chain split/rollback scenario). Once such a target exists, replaying a leaked `SignedDelegateAction` requires no cryptographic effort — the attacker/relayer only needs to wrap the untouched delegate action in a new outer transaction with a valid `block_hash` for the target chain, something `tools/mirror` already automates for ordinary transactions.

### Recommendation
Bind the `DelegateAction`/`DelegateActionV2` signature to the chain it is intended for, e.g. by including the genesis hash / chain id (or a recent `block_hash`, mirroring the outer `Transaction`) inside the `NEP-461`-tagged payload that `get_nep461_hash()` hashes and signs, so a signature produced for one network's chain identity cannot be reinterpreted as valid on a distinct network or fork sharing the same account state.

### Proof of Concept
1. On chain A, user `alice.near` signs a `DelegateAction { sender_id: alice.near, receiver_id: bob.near, actions: [Transfer(1000)], nonce: N, max_block_height: H, public_key: alice_pk }` and hands the `SignedDelegateAction` to relayer R, following the same construction shown in `meta_tx_from_actions` [7](#0-6) .
2. R wraps it in an outer `Transaction` with chain A's `block_hash`, submits it; it executes, transferring funds once, and advances `alice.near`'s access-key nonce past `N` on chain A only.
3. Chain B is forked/mirrored from chain A's state prior to step 2 (e.g., via `tools/mirror`'s `dump-state`/`prepare` workflow) so that `alice.near`'s access-key nonce on chain B is still below `N`.
4. Any party who observed the `SignedDelegateAction` bytes (unmodified — no re-signing needed) wraps them in a new outer `Transaction` addressed to `bob.near` with a fresh, valid `block_hash` for chain B, and submits it via RPC.
5. `apply_delegate_action` verifies the untouched signature via `signed_delegate_action.verify()` [8](#0-7) , passes the nonce/height checks in `validate_delegate_action_key` [5](#0-4) , and executes the transfer a second time on chain B — a replay the outer-transaction `block_hash` mechanism was supposed to prevent but which the inner `DelegateAction` never implements.

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

**File:** core/primitives/src/action/delegate.rs (L353-357)
```rust
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
        let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
        hash(&bytes)
    }
```

**File:** core/primitives/src/transaction.rs (L121-140)
```rust
#[derive(BorshSerialize, BorshDeserialize, PartialEq, Eq, Debug, Clone, ProtocolSchema)]
pub struct TransactionV1 {
    /// An account on which behalf transaction is signed
    pub signer_id: AccountId,
    /// A public key of the access key which was used to sign an account.
    /// Access key holds permissions for calling certain kinds of actions.
    pub public_key: PublicKey,
    /// Nonce is used to determine order of transaction in the pool.
    /// It increments for a combination of `signer_id` and `public_key`,
    /// and for gas key it also includes a `nonce_index`.
    pub nonce: TransactionNonce,
    /// Receiver account for this transaction
    pub receiver_id: AccountId,
    /// The hash of the block in the blockchain on top of which the given transaction is valid
    pub block_hash: CryptoHash,
    /// A list of actions to be applied
    pub actions: Vec<Action>,
    /// Controls nonce validation mode (monotonic or strict sequential).
    pub nonce_mode: NonceMode,
}
```

**File:** tools/mirror/README.md (L14-20)
```markdown
The first approach we might try is to just send the source chain
transactions byte-for-byte unaltered to the target chain. This almost
works, but not quite, because the `block_hash` field in the
transactions will be rejected. This means we have no choice but to
replace the accounts' public keys in the original forked state, so
that we can sign transactions with a valid `block_hash` field. So the
way we'll use this is that we'll generate the forked state from the
```

**File:** runtime/runtime/src/actions.rs (L474-477)
```rust
    if !signed_delegate_action.verify() {
        result.result = Err(ActionErrorKind::DelegateActionInvalidSignature.into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L479-482)
```rust
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L605-655)
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
```

**File:** integration-tests/src/env/test_env.rs (L778-799)
```rust
        let delegate_action = DelegateAction {
            sender_id: inner_signer.get_account_id(),
            receiver_id,
            actions: actions
                .into_iter()
                .map(|action| NonDelegateAction::try_from(action).unwrap())
                .collect(),
            nonce: user_nonce,
            max_block_height: tip.height + 100,
            public_key: inner_signer.public_key(),
        };
        let signature = inner_signer.sign(delegate_action.get_nep461_hash().as_bytes());
        let signed_delegate_action = SignedDelegateAction { delegate_action, signature };
        SignedTransaction::from_actions(
            relayer_nonce,
            relayer,
            sender,
            &relayer_signer,
            vec![Action::Delegate(Box::new(signed_delegate_action))],
            tip.last_block_hash,
        )
    }
```
