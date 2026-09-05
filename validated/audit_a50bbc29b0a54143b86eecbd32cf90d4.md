### Title
Signer weight double-counted across acceptance and rejection tallies in `StackerDBListener`, causing miner-local threshold verdicts to diverge - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
The reported bug class is: an accumulator variable (`_lockedETH`) is incremented on one event but never corrected/decremented when a later event supersedes or reverses the original contribution, leaving the accumulator in a state inconsistent with reality. The `stacks-core` analog is in `StackerDBListener::process_stackerdb_event` (stacks-node/src/nakamoto_node/stackerdb_listener.rs), which tracks `BlockStatus.total_weight_approved` and `BlockStatus.total_weight_rejected` as two independent running sums, gated by two independent tracking sets (`gathered_signatures`/`BTreeMap<u32, MessageSignature>` for acceptance, and `responded_signers`/`HashSet<u32>` for rejection). A signer that first rejects and later accepts (or vice versa) has its weight added to both tallies, because the code that adds to `total_weight_approved` only checks `!block.gathered_signatures.contains_key(&slot_id)` [1](#0-0)  while the code that adds to `total_weight_rejected` only checks `block.responded_signers.insert(slot_id)` [2](#0-1) . Neither branch subtracts the signer's weight from the *other* tally when it flips its vote.

### Finding Description
`BlockStatus` maintains two separate weight sums that are supposed to represent a partition of the signer set for a given block (approved vs. rejected) [3](#0-2) .

On `BlockAccepted`:
```
if !block.gathered_signatures.contains_key(&slot_id) {
    block.total_weight_approved = block.total_weight_approved.saturating_add(signer_entry.weight);
    ...
}
block.gathered_signatures.insert(slot_id, signature);
block.responded_signers.insert(slot_id);
``` [4](#0-3) 

On `BlockResponse::Rejected`:
```
if block.responded_signers.insert(slot_id) {
    block.total_weight_rejected = block.total_weight_rejected.saturating_add(signer_entry.weight);
    ...
}
``` [2](#0-1) 

Both branches call `responded_signers.insert(slot_id)`, which is a set — inserting the same `slot_id` twice returns `false` the second time. Consequently:
1. Signer S sends `Rejected` first: `responded_signers.insert` returns `true` (first time) → `total_weight_rejected += weight(S)`.
2. Signer S later sends `BlockAccepted` for the same block: `gathered_signatures.contains_key(&slot_id)` is `false` (this map was never touched by the rejection path) → `total_weight_approved += weight(S)`. The `responded_signers.insert(slot_id)` call inside the acceptance branch now returns `false`, but that return value is discarded — it doesn't stop the approval increment because the approval increment is gated on `gathered_signatures`, not `responded_signers`.

The result: `weight(S)` is counted in *both* `total_weight_approved` and `total_weight_rejected` simultaneously, and it is never removed from the rejection side. The reverse order (accept-then-reject) has the same effect. This breaks the invariant that `total_weight_approved + total_weight_rejected <= total_weight`, i.e., the equality that the sum of unique-signer weights on each side of the vote should partition — not double-count — the total.

### Impact Explanation
`total_weight_approved` and `total_weight_rejected` are each compared against `self.weight_threshold`/blocking-minority thresholds by `SignerCoordinator::get_block_status` to decide whether the miner should treat the block as **accepted** (`total_weight_approved >= self.weight_threshold`) or **rejected** (`total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`) [5](#0-4) . Because a flipped-vote signer's weight inflates both sums independently, and because different miner/coordinator instances observe StackerDB messages in different orders (each polls the DB independently, `EVENT_RECEIVER_POLL`), it is possible for the accept-threshold to be crossed on one node's view while another node's view instead crosses the reject-threshold first, depending on the interleaving of the flip-vote signer's two messages relative to other signers' single votes. This is a minority-triggerable (a single signer sending a stale/duplicate vote of the opposite kind — which can legitimately happen on reconsideration, or be induced by an attacker with control of just one signer key/slot) divergence in a consensus-relevant local verdict — i.e., a temporary tip/verdict disagreement between nodes about whether the same block proposal was approved or rejected, which is explicitly listed as an in-scope High-severity impact.

### Likelihood Explanation
This requires no majority collusion — a single signer sending a `Rejected` response and later a valid `BlockAccepted` signature for the *same* `block_signer_sighash` (or vice versa) is sufficient, and signers are explicitly allowed to change their minds on reconsideration of a proposal (the signer-side code itself supports revising responses on later proposals for the same block, per the `handle_block_pre_commit`/`store_and_process_block_signature` reconsideration logic referenced in `stacks-signer/src/v0/signer.rs`). The condition depends only on message arrival ordering across independent listener instances, which is inherent to the asynchronous StackerDB polling design and not something any single node controls.

### Recommendation
Track approval and rejection under a single per-slot "current vote" state (e.g., `HashMap<u32, VoteKind>` weight the tally is derived from) rather than two independently-gated sums. When a signer's vote flips, subtract its weight from the previous tally before adding it to the new one, mirroring how `refundPlayers` should have subtracted the refunded amount from `_lockedETH`. Concretely, before incrementing `total_weight_approved` on a `BlockAccepted` for a `slot_id` already present in `responded_signers` as a rejecter, first decrement `total_weight_rejected` by that signer's weight (and symmetrically for rejection after an earlier acceptance).

### Proof of Concept
1. Configure a signer set where signer `A` has weight 30, and the remaining signers collectively have weight 70 (total 100), with `weight_threshold` computed for 70% approval / 30% blocking-minority.
2. Signer `A` sends `BlockResponse::Rejected` for block `B` → `total_weight_rejected = 30`.
3. Before the miner's threshold is reached from other rejecters, signer `A` reconsiders and sends a valid `BlockAccepted` signature for the same `block_signer_sighash` → because `gathered_signatures` does not yet contain `A`'s `slot_id`, `total_weight_approved += 30` as well, while `total_weight_rejected` remains at 30 (never decremented).
4. If 70 more weight of other signers accept, `total_weight_approved` reaches 100 (crossing the 70 threshold) while `total_weight_rejected` remains at 30 — but a differently-timed node that received `A`'s rejection after computing an intermediate reject-crossing state (e.g., if 41 more weight had rejected before A's acceptance arrived) would instead cross the reject blocking-minority threshold first, since `A`'s 30 is counted there too. Two coordinator instances, differing only in message arrival order, can thus reach opposite verdicts (`Ok(signatures)` accept vs. `Err(SignersRejected)`) for the same block proposal.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L70-82)
```rust
#[derive(Debug, Clone)]
pub struct BlockStatus {
    /// Set of the slot ids of signers who have responded
    pub responded_signers: HashSet<u32>,
    /// Map of the slot id of signers who have signed the block and their signature
    pub gathered_signatures: BTreeMap<u32, MessageSignature>,
    /// Total weight of signers who have signed the block
    pub total_weight_approved: u32,
    /// Total weight of signers who have rejected the block
    pub total_weight_rejected: u32,
    /// Per-txid rejection tracking from signers
    pub failed_txids: HashMap<Txid, FailedTxInfo>,
}
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-465)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
                        block.responded_signers.insert(slot_id);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-545)
```rust
            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();

                // Only act on failed txids that a blocking minority (>30% weight) agrees on
                let blocking_minority = self.total_weight.saturating_sub(self.weight_threshold);
                let mut temporarily_excluded_txids = HashSet::new();
                let mut permanently_excluded_txids = HashSet::new();
                for (txid, info) in &block_status.failed_txids {
                    if info.total_weight > blocking_minority {
                        // Do not perma ban txids that only a small minority of signers reported as problematic
                        // But make sure its removed from the next block proposal
                        if info.problematic_weight > blocking_minority {
                            permanently_excluded_txids.insert(txid.clone());
                        } else {
                            temporarily_excluded_txids.insert(txid.clone());
                        }
                    }
                }

                return Err(NakamotoNodeError::SignersRejected {
                    temporarily_excluded_txids,
                    permanently_excluded_txids,
                });
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
