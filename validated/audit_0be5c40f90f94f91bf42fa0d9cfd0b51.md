### Title
Locally-derived reorg-permit timing in `check_parent_tenure_choice` lets different signers disagree on whether a tenure reorg is sanctioned, producing a temporary tip split - (File: stacks-signer/src/chainstate/mod.rs, stacks-signer/src/v0/signer.rs)

### Summary
The signer's reorg-timing safety check that decides whether one tenure may legitimately replace ("supersede") another is evaluated from **per-signer local state** (`local_block_info.approved_time`, i.e., when *this* signer personally approved the proposal) rather than from a globally agreed fact. Because different signers can have different local approval histories for the same proposal, they can reach different verdicts on the identical objective reorg attempt — one signer records a standing "reorg permit" (`mark_tenure_superseded`) and signs the replacement block, while another signer, having a different local timestamp, refuses. This mirrors the external report's bug class: a state committed ahead of time (`commitSplit` there, `mark_tenure_superseded`/reorg-permit here) is later relied on unconditionally to bypass a protection (frontrun protection there, double-sign/conflict protection here) without re-validating that all observers agree the precondition still holds.

### Finding Description
`check_parent_tenure_choice` in `stacks-signer/src/chainstate/mod.rs` decides whether a miner may build off something other than the prior sortition (i.e., reorg an already-mined tenure). The decision hinges on `proposal_to_sortition`, computed as: [1](#0-0) 

If this signer never personally signed/approved the reorged tenure's first block, `approved_time` is `None` and `proposal_to_sortition` is hard-coded to `0`, which is always `< first_proposal_burn_block_timing`, so the reorg is **always** permitted and the tenure is pushed to `superseded_tenures`. But if this signer *did* approve early, its own `approved_time` may yield a `proposal_to_sortition` that exceeds the threshold, causing the *same* objective reorg attempt to be **refused** by that signer.

This per-signer local record then feeds `mark_tenure_superseded`, which is later consulted by `reorg_permit_stands` (`stacks-signer/src/v0/signer.rs`): [2](#0-1) 

and consumed at the pre-commit signing gate: [3](#0-2) 

A signer whose local record shows a standing permit excludes the conflicting (superseded) block entirely and signs the replacement; a signer without that local record (because it happened to have approved the original tenure's block earlier, or never received/processed the same view) keeps blocking. Because the "sanctioning" record (`mark_tenure_superseded`, `docs/signer-flows.md` lines 496-511) is explicitly a signer-local decision — "One decision does have to be recorded, because it is ours rather than the node's" — there is no cross-signer reconciliation of *whether the permit was granted at all*, only of whether the permitting sortition (once granted) is *still canonical* (`reorg_permit_stands`). The equality that should hold — "all signers treat the same objective reorg attempt identically" — is not enforced, because it depends on each signer's private history of when it happened to approve a proposal.

### Impact Explanation
This breaks the "signer weight/threshold agreement" equality in the same class as a validation verdict two nodes/signers disagree on. A minority of signers reaching >30% weight could refuse to sign block B (believing tenure A's reorg is unsanctioned) while a different set of signers (with different local approval timing) signs and pushes B, or vice versa. This yields a temporary tip disagreement among the signer set — matching the "High" impact bucket ("a minority-triggerable ... static-validation divergence ... temporary tip disagreement"). It does not require a majority: any signer subset whose local approval timestamps diverge from another subset's can independently reach opposite reorg-permit verdicts for the identical burn/tenure sequence, without any adversarial coordination or privileged access — only ordinary asynchronous message delivery timing (a proposal reaching some signers before others near a sortition boundary) is needed.

### Likelihood Explanation
The condition is entirely dependent on ordinary network/timing variance (which signers happened to approve/sign a proposal before a competing sortition landed), not on any adversarial control of stake or keys, making it plausible in normal operation especially near tenure-extension/reorg boundaries that the codebase's own tests (`stacks-node/src/tests/signer/v0/reorg.rs`, `capitulate_parent_tenure_view.rs`) show are already timing-sensitive and exercised by 50/50 split scenarios (`pre_commit_50_50_split_agrees_on_node_tip`, `deadlock_50_50_split_capitulates_to_node_tip`), confirming the underlying mechanism is fragile to timing divergence even in the project's own test design.

### Recommendation
Do not let `proposal_to_sortition`/reorg-permit determination be a signer-local fact derived from `local_block_info.approved_time`. Either (a) base the eligibility computation on an objective, node-verifiable timestamp (e.g., burn-block-derived timing rather than "when I personally approved"), so all signers necessarily compute the identical verdict, or (b) require the permit to be broadcast/attested and cross-checked against the signer set (analogous to pre-commit weight thresholds) before a signer treats a conflict as excluded, rather than trusting a private local record.

### Proof of Concept
Deterministic PoC requires simulating signer timing divergence across the `stacks-signer` state machine (not purely a unit-level check), which needs multi-node/timing orchestration beyond static analysis. Conceptually:
1. Miner A produces tenure-A's first block; signer S1 promptly validates and signs it (`approved_time` set); signer S2 is delayed and has not yet approved it when the next sortition fires.
2. Miner B wins the next sortition and proposes a block building off a different (earlier) tenure, reorging tenure A.
3. `check_parent_tenure_choice` on S2 sees `local_block_info.approved_time == None` → `proposal_to_sortition = 0` → always permits the reorg, superseding tenure A and later returns `true` from `reorg_permit_stands`, so S2 signs the replacement.
4. `check_parent_tenure_choice` on S1 sees its own real `approved_time`, computes a larger `proposal_to_sortition` that exceeds `first_proposal_burn_block_timing`, refuses the reorg, and later `reorg_permit_stands` finds no permit recorded for tenure A (since S1 never called `mark_tenure_superseded`), so S1 keeps blocking the replacement via `get_signed_conflicts`/`conflict_still_blocks`.
5. Result: S1 and S2 disagree over which of tenure A's block or the replacement block should be signed — the signer set is split on the canonical tip until timeouts/freshness rules resolve it.

### Citations

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

**File:** stacks-signer/src/v0/signer.rs (L1222-1247)
```rust
    fn reorg_permit_stands(
        &self,
        stacks_client: &StacksClient,
        conflict: &SignedConflictInfo,
    ) -> bool {
        let Some(superseded_by) = &conflict.superseded_by else {
            return false;
        };
        match stacks_client.get_sortition_by_burn_hash(&superseded_by.burn_block_hash) {
            Ok(_) => true,
            Err(ClientError::RequestFailure(reqwest::StatusCode::NOT_FOUND)) => {
                info!("{self}: The tenure we permitted to reorg a conflicting block's tenure was itself orphaned by a burnchain fork. The permit no longer excludes the conflict.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                    "superseded_by_burn_block_hash" => %superseded_by.burn_block_hash,
                );
                false
            }
            Err(e) => {
                warn!("{self}: Failed to check whether the sortition that permitted a reorg is still canonical: {e:?}. Treating the permit as void.";
                    "conflicting_consensus_hash" => %conflict.consensus_hash,
                    "superseded_by_consensus_hash" => %superseded_by.consensus_hash,
                );
                false
            }
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1374-1421)
```rust
        // tenures whose reorg we sanctioned under the reorg-timing rules are excluded, but
        // only while the sortition the permit was granted to is still canonical
        // (`check_parent_tenure_choice` records the permit, `reorg_permit_stands` re-derives
        // its validity from the node); every other question about whether a conflict is
        // still live is derived from the node in `conflict_still_blocks`.
        //
        // Unlike the chainstate check above, a refusal here is "for now" rather than a
        // broadcast rejection: a later pre-commit re-evaluation may still sign the block once
        // the conflicting signature has gone stale.
        let conflicts = match self
            .signer_db
            .get_signed_conflicts(block_info.block.header.chain_length, &block_hash)
        {
            Ok(conflicts) => conflicts,
            Err(e) => {
                warn!("{self}: Failed to query the signed blocks. Refusing to sign block {block_hash}: {e:?}");
                return;
            }
        };
        let freshness_cutoff = get_epoch_time_secs().saturating_sub(
            self.proposal_config
                .tenure_last_block_proposal_timeout
                .as_secs(),
        );
        // A fresh signature only blocks while the block it covers could still be part of the
        // chain: see `conflict_still_blocks`, which asks the node whether it is. Check
        // freshness first: it is a local timestamp comparison, while `reorg_permit_stands`
        // and `conflict_still_blocks` each query the node, so stale conflicts cost no
        // round-trips.
        if let Some(conflict) = conflicts.iter().find(|conflict| {
            conflict.last_endorsed > freshness_cutoff
                && !self.reorg_permit_stands(stacks_client, conflict)
                && self.conflict_still_blocks(
                    stacks_client,
                    conflict,
                    block_info.block.header.chain_length,
                )
        }) {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but we have recently signed or accepted a different block at the same or higher height. Refusing to sign.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "conflicting_signer_signature_hash" => %conflict.signer_signature_hash,
                "conflicting_block_height" => conflict.stacks_height,
                "conflicting_consensus_hash" => %conflict.consensus_hash,
            );
            return;
        }
```
