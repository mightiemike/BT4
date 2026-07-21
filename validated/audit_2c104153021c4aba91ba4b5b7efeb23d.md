### Title
`build_precommit_vote_message_digest` omits `chain_id`, `height`, and `round` from the signed preimage, enabling cross-chain and cross-height/round signature replay — (`File: crates/apollo_signature_manager/src/signature_manager.rs`)

---

### Summary

`build_precommit_vote_message_digest` constructs the ECDSA preimage for a consensus precommit vote by hashing only the static domain tag `PRECOMMIT_VOTE` and the `block_hash`. It omits `chain_id`, block `height`, and consensus `round`. Because the `Vote` struct carries all four of those fields but the signature only commits to one (`proposal_commitment` / `block_hash`), a valid precommit signature for block hash X is cryptographically indistinguishable from a valid precommit for the same hash at any other height, round, or chain. When the planned signature-verification path (`// TODO(Asmaa): verify the signature`) is activated, an adversary can replay a legitimately obtained precommit signature across heights, rounds, or chains to forge quorum-contributing votes.

---

### Finding Description

`build_precommit_vote_message_digest` in `crates/apollo_signature_manager/src/signature_manager.rs`:

```rust
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);   // static tag only
    message.extend_from_slice(&block_hash);       // proposal_commitment only
    MessageDigest(blake2s_to_felt(&message))
}
```

The `Vote` struct (`crates/apollo_protobuf/src/consensus.rs`) carries five semantically distinct fields:

```rust
pub struct Vote {
    pub vote_type: VoteType,
    pub height: BlockNumber,
    pub round: Round,
    pub proposal_commitment: Option<ProposalCommitment>,
    pub voter: ContractAddress,
    pub signature: RawSignature,
}
```

Only `proposal_commitment` (the block hash) enters the signed digest. `height`, `round`, `vote_type`, and `chain_id` are absent. The signing entry point `sign_precommit_vote(block_hash)` and the verification entry point `verify_precommit_vote_signature(block_hash, sig, pk)` both call through this function, so the defect is present in both the signing and verification paths.

The consensus manager already has a live TODO marking where verification will be wired in:

```rust
// crates/apollo_consensus/src/single_height_consensus.rs
pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
    // TODO(Asmaa): verify the signature
```

Once that TODO is resolved and `verify_precommit_vote_signature` is called inside `handle_vote`, the incomplete preimage becomes an active vulnerability.

---

### Impact Explanation

**Cross-chain replay.** A validator operating on both mainnet and a testnet (or two forks sharing a common block hash) signs `PRECOMMIT_VOTE || block_hash`. Because `chain_id` is absent, the same `(sig, block_hash)` pair satisfies `verify_precommit_vote_signature` on any chain that produces the same block hash. An adversary who observes a legitimate precommit on one chain can inject it as a valid precommit on another, contributing to a false quorum.

**Cross-height / cross-round replay.** Because `height` and `round` are absent, a precommit signature obtained for block hash X at height H, round R is equally valid at height H′, round R′ if the same block hash appears. An adversary can replay a validator's old precommit at a new height or round to artificially inflate the precommit count toward quorum.

**Vote-type confusion.** The `PRECOMMIT_VOTE` tag distinguishes precommits from peer-identity messages (`INIT_PEER_ID`), but there is no corresponding `PREVOTE` tag or separate signing path for prevotes. If a prevote signing function is later added without its own tag, a precommit signature could be accepted as a prevote or vice versa.

Impact category: **High — signature/hash logic binds the wrong executable payload** (missing `chain_id`, `height`, `round` from the signed preimage).

---

### Likelihood Explanation

The vulnerability is latent today because `handle_vote` does not yet call `verify_precommit_vote_signature`. However:

- The signing infrastructure (`sign_precommit_vote`, `verify_precommit_vote_signature`) is already wired into the `SignatureManager` component and its public types (`apollo_signature_manager_types`).
- The TODO comment in `single_height_consensus.rs` explicitly marks the gap as planned work.
- Once verification is enabled, any network participant who has observed a legitimate precommit for a given block hash can replay it at a different height or round with zero additional cryptographic work.

---

### Recommendation

Include `chain_id`, `height`, `round`, and `vote_type` in the signed preimage so that each signature is bound to exactly one vote context:

```rust
fn build_precommit_vote_message_digest(
    chain_id: &ChainId,
    height: BlockNumber,
    round: Round,
    block_hash: BlockHash,
) -> MessageDigest {
    let mut message = Vec::new();
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&chain_id.as_hex().as_bytes()); // or canonical felt encoding
    message.extend_from_slice(&height.0.to_be_bytes());
    message.extend_from_slice(&round.to_be_bytes());
    message.extend_from_slice(&block_hash.to_bytes_be());
    MessageDigest(blake2s_to_felt(&message))
}
```

Update `sign_precommit_vote` and `verify_precommit_vote_signature` to accept and forward these fields. Apply the same pattern to any future prevote signing function, using a distinct domain tag (e.g., `PREVOTE_VOTE`).

---

### Proof of Concept

1. Validator V signs a precommit for block hash `H` at height 5, round 0 on chain A. The digest is `blake2s("PRECOMMIT_VOTE" || H)`.
2. Chain B (different `chain_id`) happens to produce the same block hash `H` at height 10, round 2.
3. An adversary submits the `(sig_V, H)` pair as a `Vote { vote_type: Precommit, height: 10, round: 2, proposal_commitment: H, voter: V, signature: sig_V }` to chain B's consensus manager.
4. When `handle_vote` calls `verify_precommit_vote_signature(H, sig_V, pk_V)`, it recomputes `blake2s("PRECOMMIT_VOTE" || H)` — identical to what V signed — and returns `true`.
5. The forged vote is accepted as a legitimate precommit from V, contributing to quorum on chain B at height 10, round 2, without V ever having voted there.

The same replay works across heights and rounds on the same chain whenever the same block hash recurs (e.g., empty blocks with identical state roots). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L31-33)
```rust
// Message domain separators.
pub(crate) const INIT_PEER_ID: &[u8] = b"INIT_PEER_ID";
pub(crate) const PRECOMMIT_VOTE: &[u8] = b"PRECOMMIT_VOTE";
```

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L68-74)
```rust
    pub async fn sign_precommit_vote(
        &self,
        block_hash: BlockHash,
    ) -> SignatureManagerResult<RawSignature> {
        let message_digest = build_precommit_vote_message_digest(block_hash);
        self.sign(message_digest).await
    }
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

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L241-243)
```rust
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
        trace!("Received {:?}", vote);
```
