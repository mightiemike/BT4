### Title
Precommit Vote Signature Preimage Omits Height and Round, Enabling Cross-Round/Cross-Height Replay — (`File: crates/apollo_signature_manager/src/signature_manager.rs`)

### Summary

`build_precommit_vote_message_digest` constructs the ECDSA preimage as `blake2s("PRECOMMIT_VOTE" || block_hash_bytes)`. The `Vote` struct carries `height`, `round`, and `voter` fields that are **not** committed into the digest. A valid precommit signature for `(block_hash=X, height=H, round=R)` is therefore byte-for-byte identical to a signature for `(block_hash=X, height=H′, round=R′)` for any other height or round. This is the direct Sequencer analog of the CoreWallet replay: just as the co-signer's nonce was not bound to the signing address, the precommit signature is not bound to the consensus context in which it was cast.

### Finding Description

`SignatureManager::sign_precommit_vote` delegates to `build_precommit_vote_message_digest`:

```rust
// crates/apollo_signature_manager/src/signature_manager.rs  lines 138-145
fn build_precommit_vote_message_digest(block_hash: BlockHash) -> MessageDigest {
    let block_hash = block_hash.to_bytes_be();
    let mut message = Vec::with_capacity(PRECOMMIT_VOTE.len() + block_hash.len());
    message.extend_from_slice(PRECOMMIT_VOTE);   // domain tag only
    message.extend_from_slice(&block_hash);       // proposal commitment only
    MessageDigest(blake2s_to_felt(&message))
}
```

The `Vote` type that carries this signature is:

```rust
// crates/apollo_protobuf/src/consensus.rs  lines 53-61
pub struct Vote {
    pub vote_type: VoteType,
    pub height: BlockNumber,   // NOT in preimage
    pub round: Round,          // NOT in preimage
    pub proposal_commitment: Option<ProposalCommitment>,
    pub voter: ContractAddress, // NOT in preimage
    pub signature: RawSignature,
}
```

The preimage covers only the domain tag and the proposal commitment. `height`, `round`, and `voter` are absent. The companion identity-signing function `build_peer_identity_message_digest` correctly includes both `peer_id` and `challenge` to prevent replay; the precommit path has no equivalent context binding.

The state machine already has a `TODO(Asmaa): sign the vote` / `TODO(Asmaa): verify the signature` pair, confirming that verification is planned. The `sign_precommit_vote` / `verify_precommit_vote_signature` API is the production path that will be wired in. The developer comment at line 122 also flags the ambiguity concern:

```
// TODO(noam.s): Consider wrapping each field in fixed delimiters (e.g. parentheses or tags) to
// avoid delimiter ambiguity across implementations; see apollo_propeller/signature.rs and PR review.
```

### Impact Explanation

Once signature verification is activated (the existing TODO path), an adversary who observes a valid precommit signature `σ` from validator V for `(block_hash=X, height=H, round=R)` can present `σ` as V's precommit for `(block_hash=X, height=H′, round=R′)` at any other height or round where the same proposal commitment X appears. In the reproposal flow (confirmed in the codebase: the same block content and therefore the same `proposal_commitment` is reused across rounds), precommit signatures from round 0 are cryptographically indistinguishable from signatures for round 1, 2, …, N. A malicious proposer can therefore accumulate precommit signatures across rounds and replay them to forge a quorum in a round where the honest validators did not actually vote, causing the wrong block to be finalized and producing wrong state, receipts, and events.

**Impact tier:** High — signature/hash logic binds the wrong hash/type/context for the executable consensus payload; if exploited after verification is enabled, it can cause an invalid block to be accepted, matching "Wrong state, receipt, event … from blockifier/syscall/execution logic for accepted input."

### Likelihood Explanation

- The signing infrastructure is already in production code and the verification TODO is explicitly scheduled.
- Reproposals reuse the same `proposal_commitment`, making the same `block_hash` value appear in multiple rounds within a single height — no hash collision is required.
- The attack requires only passive observation of broadcast precommit messages (public P2P traffic) plus the ability to inject a crafted `Vote` message with a replayed signature.
- No privileged access is needed.

### Recommendation

Include `height`, `round`, and `voter` in the preimage:

```rust
fn build_precommit_vote_message_digest(
    block_hash: BlockHash,
    height: BlockNumber,
    round: Round,
    voter: ContractAddress,
) -> MessageDigest {
    let mut message = Vec::new();
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(&block_hash.to_bytes_be());
    message.extend_from_slice(&height.0.to_be_bytes());
    message.extend_from_slice(&(round as u64).to_be_bytes());
    message.extend_from_slice(voter.0.key().to_bytes_be().as_ref());
    MessageDigest(blake2s_to_felt(&message))
}
```

Update `sign_precommit_vote` and `verify_precommit_vote_signature` to accept and pass these fields. This mirrors the fix in the external report (binding the signature to the signing address/nonce pair) and is consistent with how `build_peer_identity_message_digest` already binds `peer_id` and `challenge`.

### Proof of Concept

1. Validator V broadcasts a precommit for `(proposal_commitment=X, height=5, round=0)`. The wire signature is `σ = ecdsa_sign(V_priv, blake2s("PRECOMMIT_VOTE" || X))`.
2. Round 0 fails to reach quorum. The proposer reproposals the same block in round 1; `proposal_commitment` remains X.
3. Adversary constructs a `Vote { vote_type: Precommit, height: 5, round: 1, proposal_commitment: Some(X), voter: V, signature: σ }`.
4. `verify_precommit_vote_signature(BlockHash(X), σ, V_pubkey)` calls `build_precommit_vote_message_digest(BlockHash(X))` → same digest → `ecdsa_verify` returns `true`.
5. The replayed vote is accepted as V's round-1 precommit, potentially supplying the missing vote needed for a quorum that V never actually cast.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** crates/apollo_signature_manager/src/signature_manager.rs (L122-126)
```rust
// TODO(noam.s): Consider wrapping each field in fixed delimiters (e.g. parentheses or tags) to
// avoid delimiter ambiguity across implementations; see apollo_propeller/signature.rs and PR
// review.
// TODO(noam.s): replace peer_id with staker_address (or add a new
// build_staker_identity_message_digest function)
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

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L241-243)
```rust
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
        trace!("Received {:?}", vote);
```
