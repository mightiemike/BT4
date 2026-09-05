### Title
Stale duplicate-tenure check in the v1 signer chainstate path allows a rival tenure-start block to be signed over an already locally-accepted block - (File: stacks-signer/src/chainstate/v1.rs)

### Summary
`SortitionState::validate_tenure_change_payload` in the v1 chainstate module guards against a miner submitting a second, competing tenure-start (`TenureChangeCause::BlockFound`) block for a tenure that already has an accepted block, by checking `signer_db.get_last_globally_accepted_block(...)`. The v2 chainstate module's equivalent check was fixed to use `signer_db.get_last_signed_block(...)` instead, specifically because `get_last_globally_accepted_block` misses blocks sitting in `LocallyAccepted` or `PreCommitted` state and therefore incorrectly allows a duplicate tenure-change proposal through. The v1 path still contains the unfixed check.

### Finding Description
The duplicate-tenure guard is supposed to enforce "once this tenure already has an accepted block, no rival tenure-start block for the same tenure may be validated" — i.e. the tenure is "closed" to a second tenure-change block. In `stacks-signer/src/chainstate/v1.rs`: [1](#0-0) 

the code queries `get_last_globally_accepted_block`, which only sees blocks that have already been marked *globally* accepted (i.e., that a node has confirmed). A block that a quorum of signers has locally accepted, or that has only been pre-committed to, is invisible to this query, so `last_in_current_tenure` is `None` and the duplicate check passes even though the tenure already effectively has a live, signed candidate.

The v2 module fixes exactly this gap: [2](#0-1) 

using `get_last_signed_block`, and the accompanying regression test spells out the bug class explicitly: [3](#0-2) 

The shared design doc for the signer flow confirms the "duplicate check" is meant to catch exactly this case and that its correctness depends on which accepted-block query is used: [4](#0-3) 

Because v1 still calls the coarser `get_last_globally_accepted_block`, any miner can propose a second, competing tenure-start block for a tenure where a block has already been locally-accepted (but not yet globally accepted) by v1-protocol signers, and those signers' `check_proposal` will not reject it via the duplicate-tenure guard — an equality ("at most one tenure-start block may be validated per tenure once one is already accepted") that should hold is broken for any signer still running the v1 chainstate path.

### Impact Explanation
This is a minority-triggerable divergence in tenure/block validation logic between signers (or, in a mixed-version fleet, between v1 and v2 signers), not requiring any privileged key beyond that of an ordinary, permissionless miner submitting a block proposal. It can let a rival tenure-start block be pre-committed/signed on the v1 signer subset for a tenure that other signers already consider settled, producing a temporary tip disagreement / rival tenure fork among signers — matching the "High: minority-triggerable ... static-validation divergence, temporary tip disagreement" impact tier.

### Likelihood Explanation
Triggering requires only that: (1) at least one signer is still running/reachable via the v1 chainstate path, (2) a block for a tenure has reached `LocallyAccepted` or `PreCommitted` state on that signer without yet being globally accepted, and (3) the miner (or a malicious actor controlling block proposals) submits a second tenure-start block for the same tenure before global acceptance occurs. All of this is achievable by an unprivileged miner during normal tenure-start races, so likelihood is non-trivial in any deployment where v1-protocol signers are still active.

### Recommendation
Change `validate_tenure_change_payload` in `stacks-signer/src/chainstate/v1.rs` to use `signer_db.get_last_signed_block` (matching the v2 fix) instead of `get_last_globally_accepted_block`, so the duplicate-tenure guard also covers `LocallyAccepted` and `PreCommitted` blocks, and add a v1 regression test mirroring `check_tenure_change_rejects_when_locally_accepted_block_exists`.

### Proof of Concept
1. Run a signer on the v1 chainstate path (`SortitionState`/`chainstate/v1.rs`).
2. Have a miner propose a tenure-start block `A` for tenure `T`; the signer locally accepts it (`mark_locally_accepted`), reaching `LocallyAccepted` (or `PreCommitted`) state without yet reaching global acceptance.
3. Have the miner (or an attacker who can influence block proposals, e.g. after a stalled network segment) propose a second tenure-start block `B` for the same tenure `T` with a different transaction set.
4. `validate_tenure_change_payload` calls `signer_db.get_last_globally_accepted_block(&block.header.consensus_hash)`, which returns `None` because `A` is only locally accepted/pre-committed, not globally accepted.
5. The duplicate check is skipped, `check_proposal` continues, and the v1 signer may sign/pre-commit `B`, producing two live tenure-start candidates for tenure `T` across the signer set.

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L505-518)
```rust
        let last_in_current_tenure = signer_db
            .get_last_globally_accepted_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L340-357)
```rust
        // We already confirmed in check miner activity that the current tenure is valid. So check we are not
        // reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
        // here: a block we have merely pre-committed to carries no signature from us, so it is safe to
        // accept a competing tenure-start block in its place if it failed to reach consensus.
        let last_in_current_tenure = signer_db
            .get_last_signed_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/tests/v2.rs (L748-756)
```rust
/// Test that a tenure change proposal is rejected when a locally-accepted
/// (but not globally-accepted) block already exists in the same tenure.
///
/// This is a regression test: previously, the check used
/// `get_last_globally_accepted_block`, which would miss blocks in
/// `LocallyAccepted` or `PreCommitted` state and incorrectly allow
/// a duplicate tenure change.
#[test]
fn check_tenure_change_rejects_when_locally_accepted_block_exists() {
```

**File:** docs/signer-flows.md (L428-437)
```markdown
- `validate_tenure_change_payload` rejects with `DuplicateBlockFound` when we
  have already accepted a block in the tenure a tenure-change block is starting.
  v2 counts locally or globally accepted blocks (`get_last_signed_block`); v1
  counts only globally accepted ones (`get_last_globally_accepted_block`).
- the v2 `check_proposal` wrapper checks miner pubkey hash, consensus hash, the
  pox bitvec, and tenure-extend rules before delegating here.

Because the duplicate check never runs again, a block that crosses the pre-commit
threshold long after it was proposed relies on section 5's own-tenure conflict
guard to cover the same ground.
```
