Confirmed: `get_tenure_times` derives `tenure_start_time` from each signer's own locally-recorded `proposed_time` column in its `blocks` table [1](#0-0) , and `calculate_tenure_extend_timestamp` combines this local value with wall-clock reads via `get_epoch_time_secs()` to decide whether a tenure-extend is valid [2](#0-1) . This value is not derived from any consensus-committed data in the block itself.

### Title
Signer-local wall-clock timing (not consensus data) can make honest signers disagree on tenure-extend validity, causing a temporary tip split - (File: stacks-signer/src/signerdb.rs, stacks-signer/src/chainstate/v1.rs, stacks-signer/src/chainstate/v2.rs)

### Summary
The Ethereum Credit Guild report flags that Dutch-auction correctness depends on an unverified, non-deterministic external condition (sequencer liveness/time) with no on-chain anchor, so different observers of the "same" auction can reach different (bad) outcomes. The analogous defect class in this repo is that tenure-extend validity — a binary accept/reject verdict signers place on an identical block proposal — is computed from each signer's *own local* wall clock and its *own local* record of when it first observed/validated the tenure's blocks, rather than from any value carried inside the block or agreed upon by the network.

### Finding Description
When a miner proposes a full tenure-extend block, both `SortitionData::check_proposal` implementations (`v1.rs`/`v2.rs`) gate acceptance on:
```
extend_timestamp = signer_db.calculate_full_extend_timestamp(...)
enough_time_passed = get_epoch_time_secs() >= extend_timestamp
``` [3](#0-2) [4](#0-3) 

`extend_timestamp` is computed via `get_tenure_times`, which reads `proposed_time` for the most recent `GloballyAccepted` block of the tenure **as recorded in that individual signer's own `blocks` table** [5](#0-4) . `proposed_time` is stamped locally by each signer process at the moment it first observed the proposal (e.g. via `get_epoch_time_secs()` at proposal-handling time, as seen in `handle_block_proposal`) [6](#0-5) , not from the block header or any value that all signers agree on. It is then combined with `enough_time_passed = get_epoch_time_secs() >= extend_timestamp`, where the right-hand comparison is each signer's own machine clock, again a purely local, non-consensus quantity.

Consequently, the "verdict" (`Ok(())` vs `Err(RejectReason::InvalidTenureExtend)`) for the identical extend-block proposal is a function of:
1. When each individual signer's process happened to receive/process the prior tenure block (`proposed_time` skew due to normal network propagation delay), and
2. Each signer's local system clock (`get_epoch_time_secs()`), which the code assumes is synchronized but never validates (the only clock-skew tolerance enforced anywhere is the unrelated `+15s` future-timestamp check on block proposals in `postblock_proposal.rs` [7](#0-6) , which does not apply to this extend-timestamp comparison).

This breaks the intended equality "same block input ⇒ same verdict across all honest signers," because for an extend proposal submitted at a time straddling the boundary, some signers (those that observed the prior block earlier, or whose clocks run fast) will see `enough_time_passed == true` and accept, while others (later `proposed_time`, or slower clocks) will see `false` and reject with `InvalidTenureExtend` — exactly the "sequencer-uptime-unaware, purely time-gated decision with no anchor to a single canonical clock" bug class from the external report, transplanted onto signer tenure-extend validation instead of an auction price curve.

### Impact Explanation
If the population of signers straddling the timing boundary is split such that neither side alone reaches the block-acceptance weight threshold, the tenure-extend block stalls (no state change, matching the report's "auction fails to receive bids" outcome) until the miner retries after natural clock drift resolves the split, or until the next sortition. If one side does cross the acceptance threshold while a competing chain state exists (e.g. a different miner mines a competing block during the disagreement window), this manifests as the allowed "temporary tip disagreement" category: some nodes accept the extend and build on it, others reject it and treat the tenure as stalled/eligible for a different fork, producing a short-lived fork that resolves once the majority verdict propagates. No majority collusion or privileged access is required — normal network latency and unmanaged clock skew between signer machines are sufficient to trigger divergent verdicts on an identical, otherwise-valid proposal.

### Likelihood Explanation
This requires only ordinary conditions already acknowledged by the codebase's own documentation: the sample configs explicitly warn that miner/signer timeout values must be carefully coordinated or extends get erroneously "REJECTED" versus "OK" depending on relative timing [8](#0-7) [9](#0-8) . That the mechanism is *documented as fragile to timing* for the miner/signer pair, but the same fragility (proposed_time skew + unvalidated wall clocks) is silently present *between different signers* evaluating the same message, making divergence plausible under realistic network jitter (seconds-scale) rather than requiring an adversarial network partition.

### Recommendation
Do not gate the tenure-extend verdict on any signer-local wall-clock reading or signer-local `proposed_time`. Instead, derive the "time since tenure start" purely from data intrinsic to the chain (e.g., burn-block heights/timestamps already committed to consensus, as used elsewhere for `block.header.timestamp` checks) so that every signer computing the check against the same block and the same canonical burnchain view necessarily reaches the same verdict. If wall-clock comparisons cannot be avoided, bound the acceptable divergence with an explicit, network-wide clock-skew tolerance (similar to the existing 15-second future-timestamp allowance) applied symmetrically to the extend-timestamp comparison, and prefer basing `tenure_start_time`/`enough_time_passed` on the globally-accepted block's on-chain timestamp rather than each signer's private receipt time.

### Proof of Concept
1. Two honest signers, S1 and S2, are both online and synced to within normal network jitter (no attacker needed).
2. The prior tenure block propagates to S1 at t=100 and to S2 at t=102 (ordinary propagation delay). Each records its own `proposed_time` accordingly via `handle_block_proposal`/`insert_block` [10](#0-9) .
3. The miner submits a full tenure-extend proposal at wall-clock t=100+`tenure_idle_timeout`+1.
4. S1 computes `extend_timestamp = 100 + tenure_idle_timeout` (already passed) → `enough_time_passed = true` → accepts.
5. S2 computes `extend_timestamp = 102 + tenure_idle_timeout` (not yet passed by 1 second) → `enough_time_passed = false` → rejects with `RejectReason::InvalidTenureExtend`.
6. S1 and S2 broadcast opposite `BlockResponse` verdicts for the identical `signer_signature_hash`, splitting signer weight on this proposal and potentially stalling finalization or enabling a short-lived tip disagreement if a competing proposal simultaneously gathers the complementary weight.

### Citations

**File:** stacks-signer/src/signerdb.rs (L2112-2151)
```rust
    fn get_tenure_times<F>(
        &self,
        tenure: &ConsensusHash,
        cause_match: F,
    ) -> Result<(u64, u64), DBError>
    where
        F: Fn(TenureChangeCause) -> bool,
    {
        let query = "SELECT tenure_change_cause, proposed_time, validation_time_ms FROM blocks WHERE consensus_hash = ?1 AND state = ?2 ORDER BY stacks_height DESC";
        let args = params![tenure, BlockState::GloballyAccepted.to_string()];
        let mut stmt = self.db.prepare(query)?;
        let rows = stmt.query_map(args, |row| {
            let tenure_change_cause: Option<u8> = row.get(0)?;
            let tenure_change_cause = tenure_change_cause
                .and_then(|cause_byte| TenureChangeCause::try_from(cause_byte).ok());
            let proposed_time: u64 = row.get(1)?;
            let validation_time_ms: Option<u64> = row.get(2)?;
            Ok((tenure_change_cause, proposed_time, validation_time_ms))
        })?;
        let mut tenure_processing_time_ms = 0_u64;
        let mut tenure_start_time = None;
        let mut nmb_rows = 0;
        for (i, row) in rows.enumerate() {
            nmb_rows += 1;
            let (tenure_change_cause, proposed_time, validation_time_ms) = row?;
            tenure_processing_time_ms =
                tenure_processing_time_ms.saturating_add(validation_time_ms.unwrap_or(0));
            tenure_start_time = Some(proposed_time);
            if let Some(tenure_change_cause) = tenure_change_cause {
                if cause_match(tenure_change_cause) {
                    debug!("Found matching tenure change block {i} blocks ago in tenure {tenure}");
                    break;
                }
            }
        }
        debug!("Calculated tenure extend timestamp from {nmb_rows} blocks in tenure {tenure}");
        Ok((
            tenure_start_time.unwrap_or(get_epoch_time_secs()),
            tenure_processing_time_ms,
        ))
```

**File:** stacks-signer/src/signerdb.rs (L2205-2246)
```rust
    fn calculate_tenure_extend_timestamp<F>(
        &self,
        tenure_idle_timeout: Duration,
        block: &NakamotoBlock,
        check_tenure_extend: bool,
        tenure_change_match: F,
    ) -> u64
    where
        F: Fn(TenureChangeCause) -> bool,
    {
        if check_tenure_extend {
            if let Some(tenure_change) = block.get_tenure_change_tx_payload() {
                if tenure_change_match(tenure_change.cause) {
                    let tenure_extend_timestamp =
                        get_epoch_time_secs().wrapping_add(tenure_idle_timeout.as_secs());
                    debug!("Calculated tenure extend timestamp for a tenure extend block. Rolling over timestamp: {tenure_extend_timestamp}");
                    return tenure_extend_timestamp;
                }
            }
        }
        let tenure_idle_timeout_secs = tenure_idle_timeout.as_secs();
        let (tenure_start_time, tenure_process_time_ms) = self.get_tenure_times(
            &block.header.consensus_hash,
            tenure_change_match,
        ).unwrap_or_else(|e| {
            error!("Error occurred calculating tenure extend timestamp: {e:?}. Defaulting to {tenure_idle_timeout_secs} from now.");
            (get_epoch_time_secs(), 0)
        });
        // Plus (ms + 999)/1000 to round up to the nearest second
        let tenure_extend_timestamp = tenure_start_time
            .saturating_add(tenure_idle_timeout_secs)
            .saturating_add(tenure_process_time_ms.div_ceil(1000));
        debug!("Calculated tenure extend timestamp";
            "tenure_extend_timestamp" => tenure_extend_timestamp,
            "tenure_start_time" => tenure_start_time,
            "tenure_process_time_ms" => tenure_process_time_ms,
            "tenure_idle_timeout_secs" => tenure_idle_timeout_secs,
            "tenure_extend_in" => tenure_extend_timestamp.saturating_sub(get_epoch_time_secs()),
            "consensus_hash" => %block.header.consensus_hash,
        );
        tenure_extend_timestamp
    }
```

**File:** stacks-signer/src/chainstate/v2.rs (L219-249)
```rust
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

**File:** stacks-signer/src/chainstate/v1.rs (L360-391)
```rust
            let sortition_consensus_hash = &proposed_by.state().data.consensus_hash;
            let tenure_tip = client.get_tenure_tip(sortition_consensus_hash)
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

**File:** stacks-signer/src/v0/signer.rs (L1606-1628)
```rust
        if block_proposal
            .block
            .header
            .timestamp
            .saturating_add(self.block_proposal_max_age_secs)
            < get_epoch_time_secs()
        {
            // Block is too old. Reject it (without validating) rather than silently
            // dropping it: the miner's proposal loop re-sends the same block until it
            // accumulates rejection weight, so a silent drop from the whole signer set
            // would livelock the tenure until the next sortition.
            warn!("{self}: Received a block proposal that is more than {} secs old. Rejecting...", self.block_proposal_max_age_secs;
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "timestamp" => block_proposal.block.header.timestamp,
            );
            let rejection =
                self.create_block_rejection(RejectReason::ProposalTooOld, &block_proposal.block);
            self.send_block_response(&block_proposal.block, rejection.into());
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1699-1719)
```rust
                    get_epoch_time_secs(),
                );
            } else {
                // Still store the block but log we can't submit it for validation. We may receive enough signatures/rejections
                // from other signers to push the proposed block into a global rejection/acceptance regardless of our participation.
                // However, we will not be able to participate beyond this until our block submission times out or we receive a response
                // from our node.
                warn!("{self}: cannot submit block proposal for validation as we are already waiting for a response for a prior submission. Inserting pending proposal.";
                    "signer_signature_hash" => signer_signature_hash.to_string(),
                );
                self.signer_db
                    .insert_pending_block_validation(&signer_signature_hash, get_epoch_time_secs())
                    .unwrap_or_else(|e| {
                        warn!("{self}: Failed to insert pending block validation: {e:?}")
                    });
            }

            // Do not store KNOWN invalid blocks as this could DOS the signer. We only store blocks that are valid or unknown.
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L657-669)
```rust
        if self.block.header.timestamp > get_epoch_time_secs() + 15 {
            warn!(
                "Rejected block proposal";
                "reason" => "Block timestamp is too far into the future",
                "block_timestamp" => self.block.header.timestamp,
                "current_time" => get_epoch_time_secs(),
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::InvalidTimestamp,
                reason: "Block timestamp is too far into the future".into(),
                failed_txid: None,
            });
        }
```

**File:** sample/conf/mainnet-miner-conf.toml (L219-231)
```text
# WARNING: Interacts with signer's `block_proposal_timeout_ms` (default 120_000ms).
# The signer independently waits `block_proposal_timeout_ms` before marking
# the new sortition winner as inactive. The signer will reject tenure extends
# from the previous miner until it has timed out the new winner.
#
# If this value < signer's `block_proposal_timeout_ms`:
#   Miner extends BEFORE signer times out the new winner -> REJECTED
# If this value >= signer's `block_proposal_timeout_ms`:
#   Signer times out new winner first, then accepts the extend -> OK
#
# Additionally, the signer requires `tenure_idle_timeout_secs + tenure_idle_timeout_buffer_secs`
# (default 32s) to have passed since the last block before accepting any extend.
# Both conditions must be met on the signer side.
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L70-78)
```text
# WARNING: Interacts with miner's `tenure_extend_wait_timeout_ms` (default 120_000ms).
# The miner waits `tenure_extend_wait_timeout_ms` before attempting to extend.
#
# If miner's value < this value:
#   Miner extends BEFORE signer invalidates the new winner -> REJECTED
# If miner's value >= this value:
#   Signer invalidates new winner first, then accepts extend -> OK
#
# Recommended: keep this <= miner's tenure_extend_wait_timeout_ms.
```
