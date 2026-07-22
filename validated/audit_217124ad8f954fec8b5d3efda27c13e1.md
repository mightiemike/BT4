### Title
Precommit Vote Signature Domain Omits `round` and `chain_id`, Enabling Cross-Round and Cross-Chain Replay — (`crates/apollo_signature_manager/src/signature_manager.rs`)

---

### Summary

`build_precommit_vote_message_digest` constructs the ECDSA preimage as `PRECOMMIT_VOTE || block_hash` only. The `round`, `height`, and `chain_id` fields present in the `Vote` struct are not bound by the signature. A valid precommit signature for a given `block_hash` is therefore cryptographically identical across every round in which that block is reproposed, and across every chain on which the same `block_hash` value appears. This is the direct sequencer analog of the ChainlinkAtlasWrapper "retransmit old report" bug: a signed artifact carries no uniqueness context, so it can be replayed without the signer's participation.

---

### Finding Description

`build_precommit_vote_message_digest` in `crates/apollo_signature_manager/src/signature_manager.rs` (lines 138–145) produces:

```
digest = blake2s( b"PRECOMMIT_VOTE" || block_hash.to_bytes_be() )
``` [1](#0-0) 

The `Vote` struct that carries this signature contains five additional fields that are **not** covered by the digest:

```rust
pub struct Vote {
    pub vote_type: VoteType,   // not signed
    pub height: BlockNumber,   // not signed
    pub round: Round,          // not signed
    pub proposal_commitment: Option<ProposalCommitment>,  // ← only this is signed
    pub voter: ContractAddress,
    pub signature: RawSignature,
}
``` [2](#0-1) 

**Cross-round replay.** The consensus manager explicitly supports reproposals: when a round fails to reach decision, the same `proposal_commitment` (block hash) is reused in a later round via `SMRequest::Repropose`. [3](#0-2) 

Because `round` is absent from the signature preimage, a precommit signature produced at round 0 is cryptographically valid for round 5 of the same block. The duplicate-vote guard in `single_height_consensus.rs` keys on `(round, voter)`, so a vote replayed into a different round is treated as a **new** vote. [4](#0-3) 

**Cross-chain replay.** `calculate_block_hash` does not include `chain_id` in the block hash preimage: [5](#0-4) 

Because neither the block hash nor the signature preimage binds `chain_id`, a precommit signature valid on testnet is also valid on mainnet for any block whose hash collides (e.g., same sequencer, same transactions, same gas prices).

**Verification is wired but not yet enforced.** The verification path exists as a library call: [6](#0-5) 

The consensus vote handler carries an explicit TODO to call it: [7](#0-6) 

The signing path is already active in production. When the TODO is resolved and `verify_precommit_vote_signature` is wired in, the broken domain will be enforced as-is, making the replay immediately exploitable.

---

### Impact Explanation

Once signature verification is enabled, an adversary who observed a validator's precommit for block_hash X at round 0 can inject that same signature as a fresh precommit at round 5 (or any later reproposal round). The state machine will accept it as a new vote from that validator. If the adversary controls or observes enough validators' round-0 precommits, they can manufacture a false 2/3+ quorum in a later round without those validators' knowledge or consent, causing the node to commit a block under a fraudulent consensus decision.

The cross-chain variant allows a testnet validator key to be used to forge mainnet precommits for any block whose hash matches.

Matching impact: **High — signature/hash logic binds the wrong signer, hash, type, or executable payload.**

---

### Likelihood Explanation

- Reproposal is a normal protocol path (round timeouts trigger it regularly).
- The signing code is already active; only the verification TODO separates the latent flaw from a live exploit.
- No privileged access is required: any network observer who records broadcast precommit messages can replay them.

---

### Recommendation

Extend `build_precommit_vote_message_digest` (and its verification counterpart) to bind all fields that distinguish one vote from another:

```rust
fn build_precommit_vote_message_digest(
    chain_id: &ChainId,
    height: BlockNumber,
    round: Round,
    block_hash: BlockHash,
) -> MessageDigest {
    let mut message = Vec::new();
    message.extend_from_slice(PRECOMMIT_VOTE);
    message.extend_from_slice(chain_id.as_hex_str().as_bytes()); // bind chain
    message.extend_from_slice(&height.0.to_be_bytes());          // bind height
    message.extend_from_slice(&round.to_be_bytes());             // bind round
    message.extend_from_slice(&block_hash.to_bytes_be());
    MessageDigest(blake2s_to_felt(&message))
}
```

Update `sign_precommit_vote` and `verify_precommit_vote_signature` to accept and pass these parameters. The existing TODO comment at `single_height_consensus.rs:242` should be resolved simultaneously so that the corrected domain is enforced from the moment verification is enabled.

---

### Proof of Concept

1. Height H, round 0: proposer broadcasts block_hash X. Validator V signs and broadcasts `precommit(X, H, round=0)` with `sig_V = sign(blake2s(b"PRECOMMIT_VOTE" || X))`.
2. Round 0 fails (not enough precommits). Consensus advances to round 5.
3. The same block X is reproposed at round 5 via `SMRequest::Repropose`.
4. Adversary injects `Vote { vote_type: Precommit, height: H, round: 5, proposal_commitment: X, voter: V, signature: sig_V }` into the network.
5. `single_height_consensus::handle_vote` checks: height matches ✓, V is a committee member ✓, `received_vote` checks `(round=5, voter=V)` — not seen before ✓.
6. When `verify_precommit_vote_signature(X, sig_V, V.pubkey)` is called, it recomputes `blake2s(b"PRECOMMIT_VOTE" || X)` — identical to what V signed — and returns `true`.
7. The replayed vote is accepted as a genuine round-5 precommit from V, contributing to a false quorum.

### Citations

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

**File:** crates/apollo_consensus/src/manager.rs (L1135-1138)
```rust
            SMRequest::Repropose(proposal_id, build_param) => {
                context.repropose(proposal_id, build_param).await;
                CONSENSUS_REPROPOSALS.increment(1);
                Ok(None)
```

**File:** crates/apollo_consensus/src/state_machine.rs (L207-224)
```rust
    pub(crate) fn received_vote(&self, vote: &Vote) -> VoteStatus {
        let determine_status = |old: &Vote, new: &Vote| {
            if old.proposal_commitment == new.proposal_commitment {
                VoteStatus::Duplicate
            } else {
                VoteStatus::Conflict(old.clone(), new.clone())
            }
        };

        // Check Map
        let key = (vote.round, vote.voter);
        let map_entry = match vote.vote_type {
            VoteType::Prevote => self.prevotes.get(&key),
            VoteType::Precommit => self.precommits.get(&key),
        };

        if let Some((old_vote, _)) = map_entry {
            return determine_status(old_vote, vote);
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
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
    ))
}
```

**File:** crates/apollo_consensus/src/single_height_consensus.rs (L241-243)
```rust
    pub(crate) fn handle_vote(&mut self, vote: Vote) -> Requests {
        // TODO(Asmaa): verify the signature
        trace!("Received {:?}", vote);
```
