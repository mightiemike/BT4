Now I have found the strongest analog. The key finding is documented directly in `docs/signer-flows.md` at lines 425-433: the tenure-extend timing rules (`enough_time_passed`/`changed_burn_view` checks in `stacks-signer/src/chainstate/v1.rs` and `v2.rs`) are enforced **only** at proposal arrival (`check_proposal`), and are explicitly **not re-run** at validate-ok or at the moment of signing (`check_block_against_signer_db_state` in `stacks-signer/src/v0/signer.rs`). [1](#0-0) [2](#0-1) 

This directly parallels the Astaria pattern: one entry point (`AstariaRouter`/here, `check_proposal`) enforces a time-based deadline gate, while the path that actually finalizes the action (`VaultImplementation`/here, `check_block_against_signer_db_state`, invoked at both validate-ok and pre-commit-threshold signing) omits it entirely.

### Title
Tenure-extend idle-timeout/burn-view gate is enforced only at proposal arrival, not at validate-ok or signing - (File: stacks-signer/src/v0/signer.rs, stacks-signer/src/chainstate/v1.rs, stacks-signer/src/chainstate/v2.rs)

### Summary
The signer's tenure-extend admission rule (`changed_burn_view || enough_time_passed || is_in_replay`) is checked exactly once, inside `check_proposal` (`chainstate/v1.rs` lines 352-403, `chainstate/v2.rs` lines 199-250), computed from a live `get_epoch_time_secs()` read at the moment the proposal is first seen. The re-check function used at every later gate — `check_block_against_signer_db_state`, run both when async node validation returns OK and again right before the pre-commit-threshold signature is cast — never re-evaluates this rule. [3](#0-2) [4](#0-3) 

### Finding Description
`check_proposal` is the only call site of the tenure-extend timing gate. As documented explicitly in `docs/signer-flows.md` lines 425-433, `check_block_against_signer_db_state` — the function invoked at validate-ok (`handle_block_validate_ok`, `stacks-signer/src/v0/signer.rs` lines ~1946-1958) and again at the pre-commit weight threshold (`stacks-signer/src/v0/signer.rs` lines 1345-1366) — only calls `check_tenure_change_confirms_parent` / `check_latest_block_in_tenure`; it never calls back into `validate_tenure_change_payload`/the tenure-extend timing branch. [5](#0-4) 

Because the timing gate is evaluated at a single wall-clock instant (`get_epoch_time_secs()` compared against `extend_timestamp`) and never reproduced later in the pipeline, individual signers can diverge on the same tenure-extend block purely based on when each one happened to receive/evaluate the proposal relative to the deadline: a signer that receives the proposal a moment before `extend_timestamp` rejects it outright in `check_proposal` (correctly, per its clock), while a signer receiving it slightly later accepts it and carries that acceptance all the way through validate-ok and signing without ever re-verifying the timing condition against its now-current clock. There is no synchronizing re-check that forces all signers who ultimately sign to have observed the same "enough time passed" fact at the moment the pre-commit threshold is crossed — only the *initial* observation is checked, and it is per-signer, wall-clock, and single-shot.

### Impact Explanation
This produces a genuine equality break in the "signer weight ≥ threshold" verdict: signers whose local clocks/arrival-times straddle the idle-timeout boundary compute different pass/fail results for the identical block, one minority-triggerable by simple network-latency variance around the deadline (no majority collusion or admin key needed). Because the check is never repeated at validate-ok or signing, a proposal that barely failed the gate for some signers and barely passed for others can still gather threshold signatures from the "passed" subset while the "failed" subset independently rejects, producing a temporary signer-set split / tip disagreement on whether the tenure-extend block is valid — bounded impact (temporary tip disagreement), matching the High severity band for this class of bug rather than Critical, since it does not by itself corrupt the node-level chainstate (the node's own block acceptance rules are unaffected; this is purely a signer-side policy gate).

### Likelihood Explanation
This requires no privileged access or majority control — it only requires natural network-timing variance for a proposal that arrives close to a signer's individually computed `extend_timestamp` boundary, which is a realistic and even expected occurrence given the explicit buffer/coordination documentation in `sample/conf/signer/mainnet-signer-conf.toml` (lines 103-121) acknowledging clock-skew concerns. Any miner (not even necessarily malicious) proposing a tenure-extend exactly around the idle-timeout boundary can trigger this divergence. [6](#0-5) 

### Recommendation
Re-run the tenure-extend timing/burn-view gate (or a documented equivalent) inside `check_block_against_signer_db_state`, or otherwise deterministically re-derive whether the deadline condition held using data captured at proposal time (rather than a live wall-clock re-read at each signer), so that all signers who ultimately sign a tenure-extend block are provably agreeing on the same "extend is permitted" fact at the moment their signature is committed, not merely at the moment the proposal happened to arrive.

### Proof of Concept
1. Configure `tenure_idle_timeout_secs` normally (e.g. 30s) across a signer set.
2. Have a miner submit a tenure-extend (`Extended` cause) block proposal timed so that `get_epoch_time_secs()` sits within roughly one network round-trip of each signer's individually computed `extend_timestamp` (`calculate_full_extend_timestamp`, `stacks-signer/src/signerdb.rs` lines 2186-2246).
3. Signers whose local evaluation of `check_proposal` (`chainstate/v1.rs`/`v2.rs`, `enough_time_passed = epoch_time >= extend_timestamp`) lands just before the threshold reject the proposal outright and never enter validate-ok/signing.
4. Signers whose evaluation lands just after the threshold accept it, proceed through validate-ok (`check_block_against_signer_db_state`) and pre-commit signing without any repeat of the timing check, and may reach the ≥70% signature threshold from among themselves.
5. Result: the signer set is split on the validity of the same tenure-extend block, one subset holding a valid signature set the other subset would have rejected had it evaluated the identical proposal — a verdict two honest, unprivileged nodes disagree on, without either node needing a majority.

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L210-249)
```rust
        // is there a full tenure extend in this block?
        if let Some(tenure_extend) = block
            .get_tenure_extend_tx_payload()
            .filter(|extend| extend.cause.is_full_extend())
        {
            // in full tenure extends, we need to check:
            // (1) if this is the most recent sortition, an extend is allowed if it changes the burnchain view
            // (2) if this is the most recent sortition, an extend is allowed if enough time has passed to refresh the block limit
            // (3) if we are in replay, an extend is allowed
            let tenure_tip = client.get_tenure_tip(tenure_id)
                .map_err(|e| {
                    warn!("Could not load current tenure tip while evaluating a tenure-extend; cannot approve."; "err" => %e);
                    RejectReason::InvalidTenureExtend
                })?;
            let Some(current_burn_view) = tenure_tip.burn_view else {
                warn!("Tenure-extend attempted in tenure without burn-view.");
                return Err(RejectReason::InvalidTenureExtend);
            };
            let changed_burn_view = tenure_extend.burn_view_consensus_hash != current_burn_view;
            let extend_timestamp = signer_db.calculate_full_extend_timestamp(
                self.config.tenure_idle_timeout,
                block,
                false,
            );
            let epoch_time = get_epoch_time_secs();
            let enough_time_passed = epoch_time >= extend_timestamp;
            let is_in_replay = self.signer_state.tx_replay_set.is_some();
            if !changed_burn_view && !enough_time_passed && !is_in_replay {
                warn!(
                    "Miner block proposal contains a tenure extend, but the conditions for allowing a tenure extend are not met. Considering proposal invalid.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "extend_timestamp" => extend_timestamp,
                    "epoch_time" => epoch_time,
                    "is_in_replay" => is_in_replay,
                    "changed_burn_view" => changed_burn_view,
                    "enough_time_passed" => enough_time_passed,
                );
                return Err(RejectReason::InvalidTenureExtend);
            }
```

**File:** stacks-signer/src/v0/signer.rs (L1799-1840)
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
                Err(e) => {
                    warn!("{self}: Error checking block proposal: {e}";
                        "signer_signature_hash" => %signer_signature_hash,
                        "block_id" => %proposed_block.block_id()
                    );
                    return Some(self.create_block_rejection(
                        RejectReason::ConnectivityIssues(
                            "error checking block proposal".to_string(),
                        ),
                        proposed_block,
                    ));
                }
            }
        }
```

**File:** stacks-signer/src/chainstate/v1.rs (L366-391)
```rust
            let Some(current_burn_view) = tenure_tip.burn_view else {
                warn!("Tenure-extend attempted in tenure without burn-view.");
                return Err(RejectReason::InvalidTenureExtend);
            };
            let changed_burn_view = tenure_extend.burn_view_consensus_hash != current_burn_view;
            let extend_timestamp = signer_db.calculate_full_extend_timestamp(
                self.config.tenure_idle_timeout,
                block,
                false,
            );
            let epoch_time = get_epoch_time_secs();
            let enough_time_passed = epoch_time >= extend_timestamp;
            let is_in_replay = replay_set.is_some();
            if !changed_burn_view && !enough_time_passed && !is_in_replay {
                warn!(
                    "Miner block proposal contains a tenure extend, but the conditions for allowing a tenure extend are not met. Considering proposal invalid.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "extend_timestamp" => extend_timestamp,
                    "epoch_time" => epoch_time,
                    "is_in_replay" => is_in_replay,
                    "changed_burn_view" => changed_burn_view,
                    "enough_time_passed" => enough_time_passed,
                );
                return Err(RejectReason::InvalidTenureExtend);
            }
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L103-121)
```text
# How much time since the last block in a tenure must pass before the
# signer will allow a tenure extend.
#
# When the signer accepts a block, it computes an extend timestamp:
#   extend_timestamp = last_block_time + tenure_idle_timeout_secs + tenure_idle_timeout_buffer_secs
# The signer includes this timestamp in its BlockAccepted response.
# The miner cannot extend until current_time >= extend_timestamp.
#
# This is one of two gates for tenure extends (the other is
# `block_proposal_timeout_ms` for new-winner invalidation).
#
# WARNING: Must coordinate with the miner's settings:
#   - Miner `tenure_timeout_secs` (default 180s): must be > this + buffer
#   - Miner `tenure_extend_wait_timeout_ms` (default 120_000ms): should
#     be >= this + buffer so the miner doesn't extend too early
#
# Default: 30
# Units: seconds
# tenure_idle_timeout_secs = 30
```
