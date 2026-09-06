### Title
Hard-coded, non-buffered 15-second future-timestamp window in block proposal validation causes signer-set verdict divergence - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`NakamotoBlockProposal::validate` rejects a block whose header timestamp is more than a fixed 15 seconds ahead of the *local* wall clock of whichever node is validating the proposal: [1](#0-0) 

This mirrors the Chainlink report's root cause pattern: a single, hard-coded threshold is applied uniformly to a check whose correct value should account for variance across the population being checked (there, differing oracle heartbeats; here, differing signer-node clocks/latencies), and the codebase elsewhere explicitly acknowledges that this class of variance needs a buffer (e.g. `tenure_idle_timeout_buffer_secs` is added "to account for clock skew between signer and miner nodes" [2](#0-1) , and the docs explicitly warn about it [3](#0-2) ) — yet the block-timestamp future-check has no equivalent skew allowance.

### Finding Description
Each stacks-node independently runs `validate()` on a proposed `NakamotoBlock` when it receives a `/v3/block_proposal` request from its local signer. The relevant check is:

```rust
if self.block.header.timestamp > get_epoch_time_secs() + 15 {
    // reject: "Block timestamp is too far into the future"
}
``` [1](#0-0) 

`get_epoch_time_secs()` reads each node's own local wall clock at the moment its own signer happened to submit the proposal for validation — a moment that varies node-to-node due to network propagation delay to each signer, per-signer processing/queueing delay, and ordinary OS clock skew. The header comment documents the intended invariant that this be a network-wide condition: "For the signers to consider a block valid, this timestamp must be: ... At most 15 seconds into the future" [4](#0-3) , i.e., every signer is expected to reach the *same* verdict on the *same* proposed block.

A miner (an unprivileged, single actor who only needs to win a sortition — no signer majority or admin key required) fully controls the proposed block's `timestamp` field and can set it to `now_miner + 15` (or arbitrarily close to the boundary as perceived by the miner's own clock). Because propagation/queueing delay to different signers' nodes is not zero and not identical, and no clock-skew buffer is added to this particular check, some signer nodes will evaluate the proposal while it is still within the 15s window (accept) and others — whose corresponding node's local clock/queue lags — will evaluate it after the window has elapsed (reject with `InvalidTimestamp`).

This breaks the equality the rest of the signing protocol depends on: that all signers converge on the same accept/reject verdict for a given proposal so that the acceptance-weight threshold (`compute_voting_weight_threshold`, used at `stacks-signer/src/v0/signer.rs` lines 2494-2514 [5](#0-4) ) is reached or not reached consistently across the honest signer set. A boundary-timed proposal can produce a split where some signers accept and sign while others reject, none of which is a majority/Byzantine assumption violation — it is triggered purely by an unprivileged miner's choice of timestamp plus ordinary network/clock variance, which the codebase elsewhere treats as a real, non-negligible factor requiring explicit buffering.

### Impact Explanation
This falls into the "temporary tip disagreement" / "minority-triggerable static-validation divergence" category explicitly allowed by the rules: two honest, correctly-behaving nodes can produce different verdicts (accept vs. `InvalidTimestamp` reject) for the identical block proposal, purely as a function of when each node's validation call happened to run relative to its own local clock. In the worst case this stalls a round (miner must re-propose, per the `ProposalTooOld`/re-mine flow documented in `proposal_replication_void.rs` [6](#0-5) ) or causes a split of pre-commits/signatures that has to heal via the existing freshness/timeout logic. It does not by itself cause a permanent chain split, block-reward theft, or an invalid block being globally accepted, because the network still requires the actual 70% weight threshold to finalize a block; the effect is bounded to delay/temporary disagreement rather than deep fork or reward loss.

### Likelihood Explanation
Likelihood is low-to-moderate: NTP-synchronized production nodes typically have sub-second clock skew, well inside the 15-second window, so under normal operating conditions this divergence would rarely manifest. However, unlike the other timing gates in this same codebase (`tenure_idle_timeout_buffer_secs`, `reorg_attempts_activity_timeout`, etc.) which explicitly add clock-skew buffers, this specific future-timestamp check has none, so any combination of larger-than-expected network latency to a subset of signers' nodes, a node under load, or modestly de-synced clocks is sufficient for a boundary-crafted proposal from an unprivileged miner to trigger the divergence. No signer majority, admin action, or other party's key is required — only a single miner choosing a timestamp near the boundary.

### Recommendation
Replace the fixed, non-buffered `+ 15` future-tolerance constant with a threshold that accounts for the same kind of skew/latency variance the project already buffers for elsewhere (e.g., add a configurable `future_timestamp_buffer_secs`, analogous to `tenure_idle_timeout_buffer_secs`), and/or anchor the "future" check against a chain-derived reference (such as the burn-block-derived time or a median of recently observed peer clock offsets) rather than each node's raw local wall clock, so that all signers converge on the same verdict for the same proposal regardless of individual clock/latency variance.

### Proof of Concept
1. A miner wins a sortition and crafts a `NakamotoBlock` with `header.timestamp = local_miner_time + 15` (the maximum allowed skew).
2. The miner submits this proposal via StackerDB to the signer set.
3. Signer A's node receives and calls `NakamotoBlockProposal::validate` while `get_epoch_time_secs()` on Signer A's node is still `<= header.timestamp`; the proposal is accepted (`stackslib/src/net/api/postblock_proposal.rs:657-669`).
4. Signer B's node, due to slightly higher network latency/queue delay/clock skew, calls the same `validate()` a few seconds later, when its local `get_epoch_time_secs()` has advanced past `header.timestamp`; the proposal is rejected with `ValidateRejectCode::InvalidTimestamp`, reason "Block timestamp is too far into the future" (same code path, same input, different verdict) — reproduced in the existing unit test pattern in `stackslib/src/net/api/tests/postblock_proposal.rs:372-514`, which shows the exact boundary behavior of this check, just without a second signer's independent clock in the test harness.
5. Signers A and B now disagree on this block's validity, splitting weight toward acceptance vs. rejection thresholds for the same proposal.

*Note: I was unable to find any configuration knob or code path that adds clock-skew buffering specifically to this future-timestamp check (searches for `MAX_BLOCK_TIME_FUTURE`, `too far into the future`, and surrounding config only surfaced the single hard-coded `15` in `postblock_proposal.rs` and its test), so I could not confirm whether a fix already exists in an untracked/newer part of the codebase outside index coverage.*

### Citations

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

**File:** stacks-signer/src/config.rs (L50-52)
```rust
/// Default number of seconds to add to the tenure extend time, after computing the idle timeout,
/// to allow for clock skew between the signer and the miner
const DEFAULT_TENURE_IDLE_TIMEOUT_BUFFER_SECS: u64 = 2;
```

**File:** sample/conf/signer/mainnet-signer-conf.toml (L123-129)
```text
# Buffer added to the tenure idle timeout to account for clock skew
# between signer and miner nodes. The effective idle timeout sent to
# miners is: tenure_idle_timeout_secs + tenure_idle_timeout_buffer_secs.
#
# Default: 2
# Units: seconds
# tenure_idle_timeout_buffer_secs = 2
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L767-771)
```rust
    /// A Unix time timestamp of when this block was mined, according to the miner.
    /// For the signers to consider a block valid, this timestamp must be:
    ///  * Greater than the timestamp of its parent block
    ///  * At most 15 seconds into the future
    pub timestamp: u64,
```

**File:** stacks-signer/src/v0/signer.rs (L2494-2514)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

        if min_weight > total_signature_weight {
            info!("{self}: Received block acceptance, but have not yet reached the acceptance threshold.";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_approved" => total_signature_weight,
                "total_weight" => total_weight,
                "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
```

**File:** stacks-node/src/tests/signer/v0/proposal_replication_void.rs (L160-188)
```rust
/// Verify that a "replication void" longer than `block_proposal_max_age_secs`
/// no longer livelocks the tenure.
///
/// Historically, signers silently dropped proposals whose header timestamp
/// was older than `block_proposal_max_age_secs`, broadcasting no rejection.
/// The miner's resend loop exits only on rejections reaching 30% weight, a
/// burn/stacks tip change, or the block appearing in the staging DB — none of
/// which can happen when every signer stays silent — so the miner re-sent the
/// same stale block forever and the tenure livelocked until the next
/// sortition. Signers now reject stale proposals with
/// `RejectReason::ProposalTooOld`, which trips the miner's rejection
/// threshold and makes it re-mine a fresh block.
///
/// Test Setup:
/// Five signers with block_proposal_max_age_secs = 30, one miner with a 15s
/// rejection timeout.
///
/// Test Execution:
/// 1. All signers ignore proposals (the void); the miner proposes block N.
/// 2. Hold the void for > 30s so the proposal goes stale, then lift it.
/// 3. The miner re-sends the stale proposal; every signer rejects it with
///    ProposalTooOld.
/// 4. The miner re-mines and the chain advances — with NO new bitcoin block.
///
/// Test Assertion:
/// - All signers reject the stale proposal with reason ProposalTooOld.
/// - The chain recovers within the same tenure (no new sortition needed) and
///   the recovery block is a fresh re-mine: different signer_signature_hash
///   and a newer header timestamp.
```
