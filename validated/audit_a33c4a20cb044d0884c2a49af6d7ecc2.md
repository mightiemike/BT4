### Title
Non-retryable classification of `SortitionViewMismatch` in `should_reevaluate_reject_reason` can permanently reject a block that becomes valid once the signer's local state catches up - (File: stacks-signer/src/v0/signer.rs)

### Summary
The GMX report describes error handlers that treat certain error classes as final ("cancel") when they are actually transient (a keeper simply provided prices/blocks outside a valid range and should be allowed to retry). The `stacks-signer` has an analogous classification list, `should_reevaluate_reject_reason`, that decides whether a previously-rejected block proposal is worth re-checking against updated local state, or whether the rejection is treated as final and no re-evaluation will ever happen even if a later, corrected proposal for the exact same block arrives.

### Finding Description
`should_reevaluate_reject_reason` in `stacks-signer/src/v0/signer.rs` partitions all `RejectReason` variants into two buckets: those considered transient (retryable) and those considered permanent (never re-checked), as seen at [1](#0-0) .

`RejectReason::SortitionViewMismatch` is placed in the "no need to re-validate" bucket alongside `RejectedInPriorRound`, `ReorgNotAllowed`, `InvalidBitvec`, etc. [2](#0-1) .

However, `SortitionViewMismatch` is produced by `check_block_against_signer_db_state`, and both of its call sites are explicitly about the signer's *own, locally observed* chain state possibly being stale or lagging relative to the network: (1) when `SortitionData::check_tenure_change_confirms_parent` finds the tenure-change block does not (yet) confirm the expected parent, and (2) when `SortitionData::check_latest_block_in_tenure` finds the proposal does not confirm as many blocks as the signer currently expects [3](#0-2) [4](#0-3) . The function's own doc comment warns this check is "an incomplete check" that must be re-derived once the signer's view of chainstate updates, i.e. it is expected to flip from mismatch to match purely as a function of the signer catching up to new blocks that arrive concurrently, not because the proposed block itself is invalid [5](#0-4) .

By contrast, connectivity/timeout/consensus-version errors (`ConnectivityIssues`, `NoSortitionView`, `NoSignerConsensus`, `UnknownParent`, `NotFoundError`) are correctly marked retryable, because they too are purely local/transient conditions [6](#0-5) . `SortitionViewMismatch` has the same "my local view was momentarily stale" character but is grouped with genuinely block-invalidating reasons like `InvalidMiner`, `PubkeyHashMismatch`, and `DuplicateBlockFound`, which are properties of the block itself and legitimately never change.

This mirrors the GMX bug class precisely: a class of error caused by the checker's transient/incomplete local state is misclassified alongside permanent-invalidity errors, causing the caller (`should_reevaluate_block`) to never re-run validation for that block even when a corrected re-proposal for the identical block arrives [7](#0-6) .

### Impact Explanation
When a signer initially rejects a proposal with `SortitionViewMismatch` merely because it had not yet processed a sibling/parent block that the rest of the network had already processed, this signer will permanently ignore all subsequent re-proposals of that exact block (same `signer_signature_hash`), even after its local chainstate catches up and would show the proposal as valid. This does not cause a chain split by itself (the rest of the network can still reach the 70% signing threshold without this one signer), but it deprives the block of that signer's vote/weight for the remainder of that block's lifetime, which is a bounded, minority-triggerable temporary tip disagreement — this signer sticks with a stale rejection while a majority of others correctly accept, potentially delaying threshold accumulation and increasing the chance of a timeout-driven fallback rejection window (`check_submitted_block_proposal`) under adversarial timing by other slow/malicious signers.

### Likelihood Explanation
Any single signer that is momentarily behind on processing concurrent sibling proposals or parent-tenure blocks (a normal, minority-triggerable condition — no majority coordination needed, just network/propagation timing between two miners' tenures, as exercised in `tenure_extend.rs` two-miner tests) can hit this path. It requires no privileged access, just ordinary block-propagation races that are explicitly tested for in this codebase (e.g. `verify_sortition_winner` sequences in `stacks-node/src/tests/signer/v0/tenure_extend.rs`).

### Recommendation
Move `RejectReason::SortitionViewMismatch` into the retryable bucket in `should_reevaluate_reject_reason` (or otherwise track "genuinely-invalid" vs. "locally-stale" sub-reasons separately), so that when a later re-proposal for the same block arrives, `should_reevaluate_block` re-runs `check_block_against_signer_db_state` rather than treating the earlier rejection as final.

### Proof of Concept
1. Signer A receives block proposal `P` (tenure-change block for tenure T2) before it has processed the last block of tenure T1.
2. `check_block_against_signer_db_state` calls `check_tenure_change_confirms_parent`, which returns `Ok(false)` because Signer A's local `signer_db`/chainstate does not yet show the expected parent confirmed — Signer A creates a `SortitionViewMismatch` rejection and stores it via `handle_block_proposal`.
3. Miner or another signer/relayer re-broadcasts the identical proposal `P` after Signer A has caught up and Signer A would now pass the check.
4. `handle_block_proposal` → `should_reevaluate_block` calls `should_reevaluate_reject_reason(block_info)`, which returns `false` for `SortitionViewMismatch`, so Signer A takes the "already responded, do nothing new" branch instead of re-running `check_block_against_signer_db_state` [7](#0-6) .
5. Signer A never re-validates or re-votes on `P`, permanently withholding its weight from a block it would otherwise correctly accept.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1505-1533)
```rust
        if !should_reevaluate_reject_reason(block_info) {
            if block_info.state == BlockState::PreCommitted {
                // We validated this block but haven't signed it. Signing requires the
                // pre-commit threshold and the conflict checks in `handle_block_pre_commit`.
                // Re-broadcast our pre-commit and re-run that evaluation instead of
                // responding with a signature directly, so a re-proposed block can't
                // bypass those checks.
                info!(
                    "{self}: received a block proposal for a block we have pre-committed to but not signed. Re-evaluating the pre-commit.";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_info.block.block_id(),
                    "block_height" => block_info.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                    "consensus_hash" => %block_info.block.header.consensus_hash
                );
                self.send_block_pre_commit(signer_signature_hash.clone());
                let address = self.stacks_address.clone();
                self.handle_block_pre_commit(
                    stacks_client,
                    sortition_state,
                    &address,
                    &signer_signature_hash,
                );
                return false;
            }
            if let Some(block_response) = self.determine_response(block_info) {
                self.send_block_response(&block_info.block, block_response);
                return false;
            } else {
```

**File:** stacks-signer/src/v0/signer.rs (L1799-1826)
```rust
    /// WARNING: This is an incomplete check. Do NOT call this function PRIOR to check_proposal or block_proposal validation succeeds.
    ///
    /// Re-verify a block's chain length against the last signed block within signerdb.
    /// This is required in case a block has been approved since the initial checks of the block validation endpoint.
    fn check_block_against_signer_db_state(
        &mut self,
        stacks_client: &StacksClient,
        proposed_block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = proposed_block.header.signer_signature_hash();
        // If this is a tenure change block, ensure that it confirms the correct number of blocks from the parent tenure.
        if let Some(tenure_change) = proposed_block.get_tenure_change_tx_payload() {
            // Ensure that the tenure change block confirms the expected parent block
            match SortitionData::check_tenure_change_confirms_parent(
                tenure_change,
                proposed_block,
                &mut self.signer_db,
                stacks_client,
                self.proposal_config.tenure_last_block_proposal_timeout,
                self.proposal_config.reorg_attempts_activity_timeout,
            ) {
                Ok(true) => return None,
                Ok(false) => {
                    return Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                }
```

**File:** stacks-signer/src/v0/signer.rs (L1842-1866)
```rust
        // Ensure that the block is the last block in the chain of its current tenure.
        match SortitionData::check_latest_block_in_tenure(
            &proposed_block.header.consensus_hash,
            proposed_block,
            &mut self.signer_db,
            stacks_client,
            self.proposal_config.tenure_last_block_proposal_timeout,
            self.proposal_config.reorg_attempts_activity_timeout,
        ) {
            Ok(is_latest) => {
                if !is_latest {
                    warn!(
                        "Miner's block proposal does not confirm as many blocks as we expect";
                        "proposed_block_consensus_hash" => %proposed_block.header.consensus_hash,
                        "proposed_block_signer_signature_hash" => %signer_signature_hash,
                        "proposed_chain_length" => proposed_block.header.chain_length,
                    );
                    Some(self.create_block_rejection(
                        RejectReason::SortitionViewMismatch,
                        proposed_block,
                    ))
                } else {
                    None
                }
            }
```

**File:** stacks-signer/src/v0/signer.rs (L2705-2739)
```rust
/// Determine if a block should be re-evaluated based on its rejection reason˝
fn should_reevaluate_reject_reason(block_info: &BlockInfo) -> bool {
    if let Some(reject_reason) = &block_info.reject_reason {
        match reject_reason {
            RejectReason::ValidationFailed(ValidateRejectCode::UnknownParent)
            | RejectReason::ValidationFailed(ValidateRejectCode::NotFoundError)
            | RejectReason::NoSortitionView
            | RejectReason::ConnectivityIssues(_)
            | RejectReason::TestingDirective
            | RejectReason::InvalidTenureExtend
            | RejectReason::ConsensusHashMismatch { .. }
            | RejectReason::NoSignerConsensus
            | RejectReason::NotRejected
            | RejectReason::Unknown(_) => true,
            RejectReason::ValidationFailed(_)
            | RejectReason::RejectedInPriorRound
            | RejectReason::SortitionViewMismatch
            | RejectReason::ReorgNotAllowed
            | RejectReason::InvalidBitvec
            | RejectReason::PubkeyHashMismatch
            | RejectReason::InvalidMiner
            | RejectReason::NotLatestSortitionWinner
            | RejectReason::InvalidParentBlock
            | RejectReason::DuplicateBlockFound
            | RejectReason::IrrecoverablePubkeyHash
            | RejectReason::ProblematicTransactions
            | RejectReason::ProposalTooOld => {
                // No need to re-validate these types of rejections.
                false
            }
        }
    } else {
        false
    }
}
```
