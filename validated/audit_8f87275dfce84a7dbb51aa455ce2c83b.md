### Title
Local-clock-based tenure-extend validation causes signer verdict divergence and temporary tip disagreement - (File: stacks-signer/src/chainstate/v2.rs)

### Summary
The signer's evaluation of a full `TenureChangeCause::Extended` proposal decides pass/fail using each signer's own local wall-clock reading compared against a timestamp derived from each signer's own locally-recorded `proposed_time`, so different honest signers can reach different accept/reject verdicts for the exact same block proposal near the idle-timeout boundary — mirroring the report's "one second after expiry" boundary-denial bug class, but here manifesting as inter-signer validation disagreement instead of a single contract-level denial.

### Finding Description
In `check_proposal`, a full tenure-extend is only approved if `changed_burn_view || enough_time_passed || is_in_replay`: [1](#0-0) 

`enough_time_passed` is computed as `get_epoch_time_secs() >= extend_timestamp`, where `extend_timestamp` comes from `SignerDb::calculate_full_extend_timestamp` → `calculate_tenure_extend_timestamp` → `get_tenure_times`: [2](#0-1) [3](#0-2) 

`tenure_start_time` in this computation is the locally-stored `proposed_time` column from the signer's own `blocks` table — i.e., the time this particular signer first saw/validated the tenure-change block, not a value derived from the burnchain or from any agreed-upon consensus state. `get_epoch_time_secs()` is likewise each signer's own local wall clock. Both inputs to the boundary comparison `epoch_time >= extend_timestamp` are therefore signer-local and can differ by network-propagation delay, clock skew, or simply because the check is evaluated at slightly different real times on different signers.

This exactly parallels the reported bug class: a state transition (`enough_time_passed` flipping from `false` to `true`) that occurs right at a boundary, where being "one tick" on either side of that boundary determines whether an otherwise-legitimate action (the miner's tenure extend) is accepted or rejected — except here the "buyer" is the miner's tenure-extend proposal and the deniers are individual signers rather than a single contract. Because the check is evaluated independently by every signer against its own local state, a proposal broadcast right at the boundary can legitimately receive `RejectReason::InvalidTenureExtend` from some signers and an approval from others for the identical block.

### Impact Explanation
If the tenure-extend proposal arrives close to the computed `extend_timestamp`, some signers will accept and sign while others reject with `InvalidTenureExtend`, splitting signing weight between "accept" and "reject" for the same block. This produces a temporary tip disagreement: part of the signer set may consider the extended tenure legitimate and continue building/signing on it, while another part refuses and instead falls back to signaling the miner as timed out / expecting a different subsequent proposal. This matches the "temporary tip disagreement" characterization allowed as a High-impact outcome, since it is a minority-triggerable divergence in a per-node validation verdict (not requiring an actor to control a majority of signing weight) — the divergence arises naturally from timing skew and can additionally be exacerbated by a miner intentionally proposing right at the computed boundary.

### Likelihood Explanation
Likelihood is moderate: this doesn't require any privileged access — any miner going through a normal, permitted tenure-extend flow near the idle-timeout boundary will trigger the divergence, and clock/latency variance across a real signer set of any size makes hitting this window plausible during ordinary operation (and trivially reproducible by a miner timing its proposal deliberately at `extend_timestamp`).

### Recommendation
Base the tenure-extend timing decision on a value that all signers can independently derive identically and deterministically (e.g., a burnchain-block-height-based check, or a value embedded/attested in the block/tenure-change payload itself and cross-checked rather than each signer's local `get_epoch_time_secs()`/local `proposed_time`), or add a tolerance band and require the timestamp basis to be derived from a canonical, shared reference (such as the burn block time recorded on-chain) rather than signer-local wall-clock and signer-local `proposed_time` bookkeeping.

### Proof of Concept
1. A miner's tenure remains idle until `tenure_start_time + tenure_idle_timeout_secs (+ processing time)` is reached, then it proposes a `TenureChangeCause::Extended` block with `burn_view_consensus_hash` unchanged from the current burn view (`changed_burn_view = false`) and not in replay (`is_in_replay = false`).
2. The miner broadcasts this proposal at (or slightly before, due to network latency) the boundary instant computed from *its own* view of when the tenure started.
3. Signer A, whose local `proposed_time` for the tenure-change block was recorded slightly earlier (fast network path) and whose wall clock is slightly ahead, computes `epoch_time >= extend_timestamp` as `true` and accepts the proposal.
4. Signer B, whose local `proposed_time` was recorded later (slow network path) or whose clock lags, computes the same comparison as `false` and returns `RejectReason::InvalidTenureExtend` from `check_proposal` (stacks-signer/src/chainstate/v2.rs:237-249).
5. The two signers now hold diverging verdicts on the identical block proposal, producing split signing weight and a temporary tip disagreement until the network re-converges (e.g., through retries, timeouts, or subsequent proposals).

### Citations

**File:** stacks-signer/src/chainstate/v2.rs (L228-249)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L2112-2152)
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
    }
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
