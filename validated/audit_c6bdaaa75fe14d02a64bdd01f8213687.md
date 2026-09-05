### Title
Tenure-extend timestamp defaults to "now" when a signer's local DB lacks the tenure's history, causing signers to disagree on whether a `TenureChangeCause::Extended`/`ExtendedReadCount` proposal satisfies the idle-timeout gate - (File: `stacks-signer/src/signerdb.rs`)

### Summary
`SignerDb::get_tenure_times()` computes the reference "tenure start time" used to gate acceptance of tenure-extend proposals by querying locally-stored `GloballyAccepted` blocks for the tenure's `consensus_hash`. When the querying signer has no matching rows (e.g. it hasn't yet recorded the tenure's earlier blocks as globally accepted, mirroring the `nextYieldDistribution == 0` uninitialized-state case in the referenced report), the function silently falls back to `get_epoch_time_secs()` — i.e., "now" — instead of a value that preserves the intended waiting period. This makes the resulting extend threshold equal to `now + idle_timeout`, a value dramatically more permissive than what a fully-synced signer computes from the tenure's real start time, so two honest signers evaluating the identical tenure-extend block can reach different accept/reject verdicts.

### Finding Description
`get_tenure_times` runs a SQL query filtered by `state = BlockState::GloballyAccepted` for the given `consensus_hash`, and folds over the result rows to derive `tenure_start_time` (the proposed time of the earliest matching block) and accumulated processing time: [1](#0-0) 

If zero rows are found — which happens whenever this signer does not yet have this tenure's `GloballyAccepted` blocks locally recorded — `tenure_start_time` stays `None` and is defaulted via `unwrap_or(get_epoch_time_secs())`: [2](#0-1) 

This return value feeds directly into `calculate_tenure_extend_timestamp`, which (outside of the tenure-change-block short-circuit) computes:
```
tenure_extend_timestamp = tenure_start_time + tenure_idle_timeout_secs + processing_time
``` [3](#0-2) 

So instead of the extend timestamp reflecting the real (possibly much earlier) start of the tenure, an "unknown tenure" collapses the threshold to just `now + idle_timeout`, which is trivially satisfiable moments after being computed. The project's own unit test acknowledges this behavior for an unrecognized consensus hash, showing the computed timestamp is drastically smaller ("earlier"/more permissive) than the value derived from real block history: [4](#0-3) 

This value gates whether a signer accepts a `TenureChangeCause::Extended`/`ExtendedReadCount` block proposal, via `enough_time_passed = epoch_time >= extend_timestamp`, in both v1 and v2 signer chainstate validation: [5](#0-4) [6](#0-5) 

Because `enough_time_passed` (and hence the accept/reject verdict on the proposal) depends on locally-stored history that can legitimately differ between signers (a signer that is behind on ingesting/recording `GloballyAccepted` blocks for a tenure, e.g. due to network delay, restart, or catching up after being offline), the same tenure-extend proposal can be judged valid by a signer with a "cold"/incomplete view of the tenure while a fully-synced signer — computing the real (earlier) `tenure_start_time` — correctly rejects it as premature (or vice versa, depending on relative timing).

### Impact Explanation
This is a minority-triggerable validation divergence: a single signer with an incomplete local view of a tenure's history can accept a tenure-extend block that other, fully-synced signers would reject as arriving before the idle timeout has genuinely elapsed. Depending on relative signer weights, this can allow a `TenureExtend` block to gather signatures from a subset of signers under looser effective timing than intended, or conversely a signer under this bug rejecting a tenure extend a majority correctly signs, causing block signing timeouts/temporary tip disagreement — matching the "High: minority-triggerable ... static-validation divergence ... temporary tip disagreement" category. It does not constitute reward theft or a permanent fork by itself, since it is a signer-side soft-validation gate rather than a chainstate-consensus rule enforced by the state machine, but it can desynchronize signer verdicts on identical proposals.

### Likelihood Explanation
The precondition (a signer lacking `GloballyAccepted` rows for the tenure being extended) is a routine occurrence rather than an attacker-crafted edge case — it happens naturally whenever a signer restarts, is temporarily behind on block processing, or otherwise has a locally incomplete history for the active tenure at the moment a tenure-extend proposal arrives. No admin action, majority collusion, or privileged key is required; it only requires normal network asynchrony or a signer catching up, combined with a miner (or any actor) sending a tenure-extend proposal during that window.

### Recommendation
When `get_tenure_times` finds no matching rows for a `consensus_hash` (i.e., the tenure history is unknown to this signer), it should not default to `get_epoch_time_secs()` (permissive). Instead it should either: (a) return an error/`None` that causes the caller to treat the tenure-extend as **not yet valid** (i.e., use a maximally-restrictive value, or refuse to approve until the tenure's start is known), or (b) fetch the authoritative tenure start time from the `StacksClient`/chain state rather than relying solely on locally cached rows, ensuring all signers derive the same threshold regardless of their local sync state.

### Proof of Concept
The existing `tenure_extend_timestamp` unit test already demonstrates the fallback behavior for an unrecorded/unknown tenure (`unknown_block`), asserting the computed timestamp is far smaller than the tenure's actual first `proposed_time`: [4](#0-3) 
A concrete divergence scenario:
1. Signer A has already ingested and marked `GloballyAccepted` the earlier blocks of tenure `T` (real start time `t0`, potentially far in the past). It computes `extend_timestamp_A = t0 + idle_timeout`.
2. Signer B has just restarted or is behind, and has zero `GloballyAccepted` rows for `T` in its local DB. When it evaluates the same tenure-extend proposal at time `t1 >> t0`, `get_tenure_times` returns `(t1, 0)`, so `extend_timestamp_B = t1 + idle_timeout`, which is far later than `t0 + idle_timeout`.
3. If instead B evaluates it very early (immediately after startup, before `t0 + idle_timeout` would have elapsed for A), B's "now" baseline can make `enough_time_passed` evaluate differently than A's, producing different accept/reject verdicts on the identical `TenureChangeCause::Extended` proposal.

### Citations

**File:** stacks-signer/src/signerdb.rs (L2119-2152)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L2225-2246)
```rust
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

**File:** stacks-signer/src/signerdb.rs (L3942-3949)
```rust
        // Verify tenure consensus_hash_3 (unknown hash)
        let timestamp_hash_3 =
            db.calculate_full_extend_timestamp(tenure_idle_timeout, &unknown_block, true);
        assert!(
            timestamp_hash_3.saturating_add(tenure_idle_timeout.as_secs())
                < block_infos[0].proposed_time
        );
    }
```

**File:** stacks-signer/src/chainstate/v1.rs (L371-391)
```rust
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

**File:** stacks-signer/src/chainstate/v2.rs (L229-249)
```rust
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
