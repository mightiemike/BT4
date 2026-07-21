### Title
`build_precommit_vote_message_digest` omits `chain_id`, `height`, and `round` from the signed preimage, enabling cross-context signature replay when vote verification is activated — (`crates/apollo_signature_manager/src/signature_manager.rs`)

---

### Summary

The `build_precommit_vote_message_digest` function constructs a precommit vote message digest as `blake2s(b"PRECOMMIT_VOTE" || block_hash_bytes)`. It omits `chain_id`, block `height`, consensus `round`, and the `voter` address from the signed preimage. The `sign_precommit_vote` API is production-wired via `SignatureManagerRequest::SignPrecommitVote`. When vote-signature verification is activated (currently deferred with `// TODO(Asmaa): verify the signature`), a precommit signature produced in one context is structurally valid in any other context that presents the same block hash, regardless of chain, height, or round.

---

### Finding Description

`build_precommit_vote_message_digest` at line 138–145 of `signature_manager.rs` constructs:

```
MessageDigest = blake2s( b"PRECOMMIT_VOTE" || block_hash.to_bytes_be() )
``` [1](#0-0) 

The `Vote` struct carries `height`, `round`, `proposal_commitment` (the block hash), `voter`, and `signature` as separate fields: [2](#0-1) 

None of `height`, `round`, `chain_id`, or `voter` are committed into the signed bytes. The `sign_precommit_vote` entry point is fully wired into the production `SignatureManager` component: [3](#0-2) 

Signature verification in `handle_vote` is explicitly deferred: [4](#0-3) 

And in `make_self_vote`, votes are emitted with an empty default signature: [5](#0-4) 

The `verify_precommit_vote_signature` function exists and is callable, but is only exercised in tests today: [6](#0-5) 

**Missing fields and their consequences:**

| Omitted field | Consequence |
|---|---|
| `chain_id` | A precommit signature produced on testnet is structurally valid on mainnet for the same block hash |
| `height` | A signature for block hash X at height H is valid at any height H′ where X reappears |
| `round` | A signature for round R is valid at any round R′ for the same block hash |
| `voter` address | The signed bytes do not bind to the specific validator; the voter field in the `Vote` message is unauthenticated |

The `chain_id` omission is the most direct analog to the external report: just as `getRequestDeploymentDigest` in `BlueprintCore.sol` omitted chain ID from the EIP-712 domain, `build_precommit_vote_message_digest` omits chain ID from the Starknet consensus vote domain.

The existing TODO comment in `signature_manager.rs` acknowledges a related ambiguity concern: [7](#0-6) 

---

### Impact Explanation

When `handle_vote` is updated to call `verify_precommit_vote_signature`, any node that captured a valid precommit signature for block hash X (e.g., from a testnet run, a prior height, or a different round) can inject it into a `Vote` message with an arbitrary `height`, `round`, or `voter` field. Because the digest does not commit to those fields, the ECDSA check passes. The consensus engine would then count the replayed vote toward quorum, potentially causing:

- Acceptance of a block at a height/round the original signer never voted for.
- Cross-chain vote injection (testnet signature accepted on mainnet) if the same block hash is constructable.
- Voter impersonation: a `Vote` message with `voter = B` carrying a signature from A's key passes verification if the verifier resolves the public key from the signature rather than from the `voter` field.

This falls under: **High — signature/hash logic binds the wrong signer, hash, type, or executable payload.**

---

### Likelihood Explanation

The block hash is computed via `calculate_block_hash`, which chains `previous_block_hash`, making accidental cross-chain or cross-height collisions practically impossible under normal operation: [8](#0-7) 

However:
1. The `chain_id` omission is a structural invariant violation that becomes exploitable the moment a testnet and mainnet share any block hash (e.g., genesis or a deliberately crafted block).
2. The `height`/`round` omission is exploitable if the same proposer re-proposes the same block hash at a different round (which the Tendermint reproposal path explicitly supports).
3. The `voter` field being unauthenticated in the signed bytes is exploitable if the verifier trusts the `voter` field to select the public key.

---

### Recommendation

Include all context-binding fields in the preimage:

```rust
fn build_precommit_vote_message_digest(
    chain_id: &ChainId,
    height: BlockNumber,
    round: Round,
    voter: ContractAddress,
    block_hash: BlockHash,
) -> MessageDigest {
    let mut message = Vec::new();
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(chain_id.as_hex().as_bytes());
    message.extend_from_slice(&height.0.to_be_bytes());
    message.extend_from_slice(&round.to_be_bytes());
    message.extend_from_slice(voter.0.key().to_bytes_be().as_ref());
    message.extend_from_slice(&block_hash.0.to_bytes_be());
    MessageDigest(blake2s_to_felt(&message))
}
```

Update `sign_precommit_vote` and `verify_precommit_vote_signature` to accept and pass these fields. Update `SignatureManagerRequest::SignPrecommitVote` to carry the full `Vote` context rather than only `BlockHash`.

---

### Proof of Concept

1. Validator A signs a precommit for `block_hash = X` at `height = 100`, `round = 0` on testnet. The digest is `blake2s(b"PRECOMMIT_VOTE" || X.to_bytes_be())`.
2. An attacker observes the signature `(r, s)` on-chain or over P2P.
3. On mainnet, the attacker constructs `Vote { vote_type: Precommit, height: 200, round: 1, proposal_commitment: Some(X), voter: A, signature: (r, s) }`.
4. When `verify_precommit_vote_signature(X, (r,s), A.public_key)` is called, it recomputes `blake2s(b"PRECOMMIT_VOTE" || X.to_bytes_be())` — identical to step 1 — and the ECDSA check passes.
5. The consensus engine counts this as a valid precommit from A at height 200, round 1, contributing to quorum for block X without A ever having voted for it. [1](#0-0) [6](#0-5)

### Citations

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L122-124)
```rust
// TODO(noam.s): Consider wrapping each field in fixed delimiters (e.g. parentheses or tags) to
// avoid delimiter ambiguity across implementations; see apollo_propeller/signature.rs and PR
// review.
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L138-145)
```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash);

    MessageDigest(blake2s_to_felt(&message))
}
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L179-186)
```rust
pub fn verify_precommit_vote_signature(
    block_hash: BlockHash,
    signature: RawSignature,
    public_key: PublicKey,
) -> SignatureVerificationResult<bool> {
    let message_digest = build_precommit_vote_message_digest(block_hash);
    verify_signature(message_digest, signature, public_key)
}
```

**File:** crates/apollo_protobuf/src/consensus.rs (L53-61)
```rust
#[derive(Debug, Default, Hash, Clone, Eq, PartialEq, Serialize, Deserialize)]
pub struct Vote {
    pub vote_type: VoteType,
    pub height: BlockNumber,
    pub round: Round,
    pub proposal_commitment: Option<ProposalCommitment>,
    pub voter: ContractAddress,
    pub signature: RawSignature,
}
```

**File:** crates/apollo_signature_manager/src/communication.rs (L30-34)
```rust
            SignatureManagerRequest::SignPrecommitVote(block_hash) => {
                SignatureManagerResponse::SignPrecommitVote(
                    self.sign_precommit_vote(block_hash).await,
                )
            }
```

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L241-243)
```rust
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
        trace!("Received {:?}", vote);
```

**File:** crates/apollo_consensus/src/state_machine.rs (L248-256)
```rust
        let vote = Vote {
            vote_type,
            height: self.height,
            round: self.round,
            proposal_commitment,
            voter: self.id,
            // TODO(Asmaa): sign the vote
            signature: RawSignature::default(),
        };
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-280)
```rust
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
```
