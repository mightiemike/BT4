### Title
Signer reorg-permission check relies on non-canonical, signer-local timing instead of a chain-verifiable proof, letting a divergent minority sanction a reorg over an already-globally-accepted tenure - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
The bridge report's root cause is that `_withdrawRequest()` accepted an "old" block as valid during a mass exit purely because compromised validators vouched for it, with no cryptographic hash-chain proof tying that old block to the point security was known-good. The stacks-signer analog is `SortitionData::check_parent_tenure_choice`, which decides whether a miner is allowed to reorg away an already mined, globally-accepted tenure. Instead of using a canonical, chain-derived, hash-linked fact, it bases the decision on **locally recorded, signer-private timestamps** (`signer_db.get_burn_block_receive_time` and `local_block_info.approved_time`) that are never committed to the chain and are not identical across signers.

### Finding Description
`check_parent_tenure_choice` in [1](#0-0)  permits a miner to reorg away a tenure that already produced a globally-accepted block as long as that tenure only produced one block and its timing was "poor" - i.e. the gap between when *this particular signer* locally recorded approving the reorged tenure's first block (`local_block_info.approved_time`) and when *this particular signer* locally recorded receiving the new burn block (`sortition_state_received_time`) is under `first_proposal_burn_block_timing`: [2](#0-1) 

Both timestamps are signer-local bookkeeping values (see `signerdb.rs`'s burn-block receive-time and block approval-time storage), not values derived from the burnchain or agreed upon on-chain. There is no hash-chain or cryptographic linkage requiring that the *specific* tenure being reorged is provably "young" relative to the sortition from the point of view of the network as a whole - only from the point of view of whichever signer is evaluating the proposal. This is exactly the class of bug in the report: an "old" (already superseded/settled) unit of chain history is revalidated as if it were new, based on a claim the validator itself makes (a locally-observed time), rather than on proof anchored to canonical chain state.

Because these clocks are per-signer, the verdict of `check_parent_tenure_choice` is **not guaranteed to agree between signers evaluating the identical tenure-change proposal**. This divergence is explicitly acknowledged and exercised by the existing test `mark_miner_as_invalid_if_reorg_is_rejected_v1`, which shows some signers approving a reorg that other signers, with different local timing state, reject for the same input: [3](#0-2) 

Because approval sanctions the reorg and marks the older tenure `superseded` (removing it from that signer's own future conflict checks, see `mark_tenure_superseded`/`get_signed_conflicts` in signerdb.rs), a set of signers that reach the timing threshold slightly differently than another set produces a genuine verdict split over whether an already-produced, globally-accepted block may be discarded.

### Impact Explanation
This is a minority-triggerable, unprivileged validation divergence: two honest signer processes evaluating the exact same tenure-change proposal can reach different `ReorgNotAllowed` verdicts purely because of local, non-canonical timing bookkeeping, not because of anything provable on-chain. This falls under the accepted category "a validation verdict two nodes disagree on," and its consequence is a temporary tip disagreement between subsets of the signer set while attempting to reorg away a tenure whose block had already been globally accepted (i.e., already relied upon by external consumers of Stacks chain state) - matching the High-impact category "temporary tip disagreement."

### Likelihood Explanation
The condition is naturally reachable without any signer compromise: normal network latency variance in when each signer's `get_burn_block_receive_time` and `approved_time` are recorded is enough to place different signers on different sides of the `first_proposal_burn_block_timing` boundary. A miner can increase the likelihood of triggering the boundary by timing its block-commit and tenure-change proposal to land close to that threshold, deliberately maximizing the chance that some signers see it as "poorly timed" (permit) and others as not (deny).

### Recommendation
Replace or supplement the signer-local timing heuristic with a canonically verifiable, chain-derived fact (e.g., a proof linking the reorged tenure to a specific burn-chain height/consensus-hash ancestry, analogous to the hash-chain proof recommended in the bridge report) so all signers evaluating the same tenure-change proposal reach an identical, reproducible verdict rather than one dependent on private local clocks.

### Proof of Concept
1. Miner 1 mines tenure A's sole block; it is globally accepted (signed by all signers).
2. Miner 1's next block-commit stalls; Miner 2 wins the next sortition and mines tenure B (no reorg).
3. Miner 1 wins a subsequent sortition quickly (within `first_proposal_burn_block_timing` for some signers but not others, due to natural network/local-clock variance) and proposes tenure C reorging away tenure B's single accepted block.
4. Each signer independently calls `check_parent_tenure_choice`; signers whose locally recorded `approved_time` for tenure B's block is close enough to their locally recorded `sortition_state_received_time` approve the reorg (marking tenure B `superseded`), while other signers, based on their own local timestamps, reject it with `ReorgNotAllowed` — exactly as demonstrated by the existing test `mark_miner_as_invalid_if_reorg_is_rejected_v1` [4](#0-3) , producing a verifiable split verdict over identical input with no cryptographic proof backing either side's decision.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L170-288)
```rust
    pub fn check_parent_tenure_choice(
        &self,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        first_proposal_burn_block_timing: &Duration,
    ) -> Result<bool, SignerChainstateError> {
        // if the parent tenure is the last sortition, it is a valid choice.
        // if the parent tenure is a reorg, then all of the reorged sortitions
        //  must either have produced zero blocks _or_ produced their first (and only) block
        //  very close to the burn block transition.
        if self.prior_sortition == self.parent_tenure_id {
            return Ok(true);
        }
        info!(
            "Most recent miner's tenure does not build off the prior sortition, checking if this is valid behavior";
            "sortition_state.consensus_hash" => %self.consensus_hash,
            "sortition_state.prior_sortition" => %self.prior_sortition,
            "sortition_state.parent_tenure_id" => %self.parent_tenure_id,
        );

        let tenures_reorged =
            client.get_tenure_forking_info(&self.parent_tenure_id, &self.prior_sortition)?;
        if tenures_reorged.is_empty() {
            warn!("Miner is not building off of most recent tenure, but stacks node was unable to return information about the relevant sortitions. Marking miner invalid.");
            return Ok(false);
        }

        // this value *should* always be some, but try to do the best we can if it isn't
        let sortition_state_received_time =
            signer_db.get_burn_block_receive_time(&self.burn_block_hash)?;

        // Track which tenures are superseded by the reorg, then mark them in
        // the DB after the reorg is permitted.
        let mut superseded_tenures = Vec::new();
        for tenure in tenures_reorged.iter() {
            if tenure.consensus_hash == self.parent_tenure_id {
                // this was a built-upon tenure, no need to check this tenure as part of the reorg.
                continue;
            }

            // disallow reorg if more than one block has already been signed
            let globally_accepted_blocks =
                signer_db.get_globally_accepted_block_count_in_tenure(&tenure.consensus_hash)?;
            if globally_accepted_blocks > 1 {
                warn!(
                    "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already more than one globally accepted block.";
                    "parent_tenure" => %self.parent_tenure_id,
                    "last_sortition" => %self.prior_sortition,
                    "violating_tenure_id" => %tenure.consensus_hash,
                    "violating_tenure_first_block_id" => ?tenure.first_block_mined,
                    "globally_accepted_blocks" => globally_accepted_blocks,
                );
                return Ok(false);
            }

            let Some(first_block_mined) = &tenure.first_block_mined else {
                // The node saw no blocks in this tenure, so the reorg takes nothing away from
                // the canonical chain. We may still hold a signature over a block in it that
                // the node has never seen (a block we accept locally is not handed to the node
                // until the whole signer set has signed it), so the reorg must still be
                // recorded if it is permitted.
                superseded_tenures.push(tenure);
                continue;
            };
            let Some(local_block_info) =
                signer_db.get_first_approved_block_in_tenure(&tenure.consensus_hash)?
            else {
                warn!(
                    "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already mined blocks, and there is no local knowledge for that tenure's block timing.";
                    "parent_tenure" => %self.parent_tenure_id,
                    "last_sortition" => %self.prior_sortition,
                    "violating_tenure_id" => %tenure.consensus_hash,
                    "violating_tenure_first_block_id" => %first_block_mined,
                );
                return Ok(false);
            };

            let checked_proposal_timing = if let Some(sortition_state_received_time) =
                sortition_state_received_time
            {
                // how long was there between when the proposal was received and the next sortition started?
                let proposal_to_sortition = if let Some(approved_at) =
                    local_block_info.approved_time
                {
                    sortition_state_received_time.saturating_sub(approved_at)
                } else {
                    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
                    0
                };
                if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
                    info!(
                        "Miner is not building off of most recent tenure. A tenure they reorg has already mined blocks, but the block was poorly timed, allowing the reorg.";
                        "parent_tenure" => %self.parent_tenure_id,
                        "last_sortition" => %self.prior_sortition,
                        "violating_tenure_id" => %tenure.consensus_hash,
                        "violating_tenure_first_block_id" => %first_block_mined,
                        "violating_tenure_proposed_time" => local_block_info.proposed_time,
                        "new_tenure_received_time" => sortition_state_received_time,
                        "new_tenure_burn_timestamp" => self.burn_header_timestamp,
                        "first_proposal_burn_block_timing_secs" => first_proposal_burn_block_timing.as_secs(),
                        "proposal_to_sortition" => proposal_to_sortition,
                    );
                    superseded_tenures.push(tenure);
                    continue;
                }
                true
            } else {
                false
            };

            warn!(
                "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already mined blocks.";
                "parent_tenure" => %self.parent_tenure_id,
                "last_sortition" => %self.prior_sortition,
                "violating_tenure_id" => %tenure.consensus_hash,
                "violating_tenure_first_block_id" => %first_block_mined,
                "checked_proposal_timing" => checked_proposal_timing,
            );
            return Ok(false);
```

**File:** stacks-node/src/tests/signer/v0/reorg.rs (L3917-3934)
```rust
}

/// Test a scenario where:
/// Two miners boot to Nakamoto.
/// Sortition occurs. Miner 1 wins.
/// Miner 1 proposes a block N
/// Signers accept and the stacks tip advances to N
/// Miner 1's block commits are paused so it cannot confirm the next tenure.
/// Sortition occurs. Miner 2 wins.
/// Miner 2 successfully mines blocks N+1
/// Miner 1 wins the next sortition, with its block commit not confirming the last tenure.
/// Miner 1 proposes block N+1'
/// 3 signers approve N+1', saying "Miner is not building off of most recent tenure. A tenure they
///   reorg has already mined blocks, but the block was poorly timed, allowing the reorg."
/// The other 2 signers reject N+1', because their `first_proposal_burn_block_timing_secs` is
///   shorter and has been exceeded.
/// Miner 1 proposes N+1' again, and all signers reject it this time.
/// Miner 2 proposes N+2, a tenure extend block and it is accepted by all signers.
```
