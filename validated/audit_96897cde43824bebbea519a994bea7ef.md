### Title
Locally-timestamped reorg-timing check in signer tenure validation causes signer-set disagreement on which tenure to endorse - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a new tenure is allowed to "reorg" (replace) a prior tenure's blocks by comparing two **locally observed, per-signer timestamps**: when that signer's own `SignerDb` recorded receiving the new burn block (`sortition_state_received_time`) and when that signer locally approved the prior tenure's first block (`local_block_info.approved_time`). If the resulting local delta is smaller than `first_proposal_burn_block_timing`, the signer treats the prior tenure's block as "poorly timed" and permits the reorg; otherwise it rejects the new tenure as building on an invalid parent. Because these timestamps are stamped independently by each signer as messages/blocks arrive over the network, different signers can legitimately compute different verdicts for the exact same objective sequence of events, producing a split signer/verdict on which tenure's blocks to sign.

### Finding Description
`check_parent_tenure_choice` in [1](#0-0)  only treats a new tenure's parent choice as automatically valid when `self.prior_sortition == self.parent_tenure_id`. Otherwise it inspects tenures being reorged and, for each one that already mined a block, computes a timing verdict from purely local state: [2](#0-1) 

`sortition_state_received_time` comes from `signer_db.get_burn_block_receive_time(&self.burn_block_hash)` — the time *this* signer's node received/observed the burn block — and `local_block_info.approved_time` is the time *this* signer locally pre-committed to the prior tenure's block. Both are wall-clock timestamps recorded independently per signer node as gossip/RPC events arrive, not a value agreed upon by the network. The function then compares their difference against the fixed threshold `first_proposal_burn_block_timing` to decide "was the prior block too late to matter" (allow reorg) vs. "prior block was on time" (reject the new tenure, i.e., keep signing the old chain).

This verdict feeds directly into `SortitionState::is_tenure_valid` [3](#0-2) , which each signer calls independently when evaluating whether to sign a proposed block/tenure. Since the underlying timestamps are local per-signer clocks/arrival times rather than a canonically-agreed value (e.g., derived from the Bitcoin block itself), a miner that (a) proposes a block close to the `first_proposal_burn_block_timing` boundary and/or (b) has any ability to influence delivery timing/ordering to different signers (e.g., selectively broadcasting, or simply benefiting from natural network latency variance across the signer set) can cause some signers to observe `proposal_to_sortition < first_proposal_burn_block_timing` (permitting a reorg onto a new tenure) while others observe the opposite (rejecting it and continuing to sign the old tenure). This is conceptually the same class of issue as CL-2020-23: because the fork-choice/validity decision is rooted in locally-observed timing of block/attestation arrival rather than a globally reproducible quantity, a minority actor can engineer disagreement about which tenure is "valid," splitting endorsement and delaying convergence — without requiring majority/Sybil control, just control over its own block-broadcast timing near the threshold.

### Impact Explanation
This does not directly cause a wrong state root or block-reward theft, but it can cause the Nakamoto signer set to disagree on which tenure/block to sign at the exact boundary of the timing window, producing a temporary tip/endorsement split across signers (some sign the reorging tenure's blocks, some continue signing/holding out for the old tenure). This matches the "High" bucket in the report's impact rubric: a minority-triggerable divergence in (in this case) signer-side static/timing validation leading to temporary tip disagreement, rather than a clean chain split, because the underlying 70%+ signer-weight threshold for actual block finalization still bounds how much damage a lone miner can do; but repeated boundary-timed proposals could still cause chronic stalls/mining-liveness degradation during contested tenure transitions.

### Likelihood Explanation
Likelihood is moderate-to-low: it requires a miner to win consecutive sortitions and to intentionally (or incidentally, via natural network jitter) time block broadcast near the `first_proposal_burn_block_timing` boundary relative to different signers' burn-block receipt times. No majority stake or signer-key compromise is needed — a single miner controlling only their own tenure and broadcast timing can attempt this repeatedly at each sortition boundary, since the local-timestamp comparison is inherently racy across a distributed signer set with no synchronization on receive time. It is a probabilistic/timing-triggerable divergence rather than a deterministic invariant break, so its practical rate depends on network latency variance and the configured `first_proposal_burn_block_timing` value.

### Recommendation
Avoid basing the reorg-permission verdict on purely local wall-clock timestamps of message arrival. Instead, root the timing decision in a value that all signers can reproduce identically (e.g., a Bitcoin-block-derived time, or a value cross-checked/attested by a threshold of signers before being trusted), or require the same threshold-signed evidence (e.g., the block's own proposal timestamp validated against the burn block's timestamp, both objective, node-verifiable quantities) rather than `get_burn_block_receive_time`/`approved_time`, which are subject to each signer's individual network conditions. Additionally, consider requiring a supermajority of signers to agree on the "late-arriving" classification before permitting a reorg, rather than allowing each signer to decide unilaterally from its own local record.

### Proof of Concept
1. Miner A wins sortition and produces the first block of tenure T at burn height `h`. Signer S1 (geographically close to Miner A) receives and locally approves this block quickly, stamping an early `approved_time`. Signer S2 (farther away / slower gossip path) receives and approves the same block later, stamping a later `approved_time`.
2. Miner B wins the next sortition at burn height `h+1` and proposes a tenure that does not build on T (attempting a reorg of T), broadcasting its tenure-change block.
3. Each signer independently runs `check_parent_tenure_choice` [2](#0-1) , computing `proposal_to_sortition = sortition_state_received_time - local_block_info.approved_time` from its own local timestamps.
4. Because S1 approved T's block earlier (larger delta) and S2 approved it later (smaller delta), S1 computes `proposal_to_sortition >= first_proposal_burn_block_timing` (rejects Miner B's reorg, keeps signing T) while S2 computes `proposal_to_sortition < first_proposal_burn_block_timing` (accepts Miner B's reorg via the `superseded_tenures.push(tenure)` path, and signs onto Miner B's tenure) — see the branch logic at [4](#0-3) .
5. Result: the signer set is split on which tenure is canonical at this boundary, delaying convergence/finalization of either fork's blocks until manual/timeout-based resolution, purely as an artifact of each signer's independent local clock/network-latency observations rather than any objective, reproducible chain data.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L170-183)
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
```

**File:** stacks-signer/src/chainstate/mod.rs (L247-278)
```rust
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
```

**File:** stacks-signer/src/chainstate/mod.rs (L581-606)
```rust
    pub fn is_tenure_valid(
        &self,
        signer_db: &mut SignerDb,
        client: &StacksClient,
        proposal_config: &ProposalEvalConfig,
        eval: &GlobalStateEvaluator,
    ) -> Result<bool, SignerChainstateError> {
        let data = self.data();
        let chose_good_parent = data.check_parent_tenure_choice(
            signer_db,
            client,
            &proposal_config.first_proposal_burn_block_timing,
        )?;
        if !chose_good_parent {
            return Ok(false);
        }
        Self::is_timed_out(
            &self.version(),
            &data.consensus_hash,
            signer_db,
            client.get_signer_address(),
            proposal_config,
            eval,
        )
        .map(|timed_out| !timed_out)
    }
```
