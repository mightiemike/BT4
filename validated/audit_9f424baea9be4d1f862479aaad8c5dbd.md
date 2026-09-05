### Title
Signer weight double-counted into both `total_weight_approved` and `total_weight_rejected` on a vote flip, breaking the disjoint-tally invariant - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener::run` tallies per-block signer weight into two counters, `total_weight_approved` and `total_weight_rejected`, which are meant to be mutually exclusive over a signer's weight (a signer either counts toward acceptance or toward rejection, never both, so that the 70%/30% supermajority math holds). The `Accepted` branch guards its weight addition on `gathered_signatures.contains_key(&slot_id)` only, not on the shared `responded_signers` set that the `Rejected` branch uses, so a single signer that first rejects and later accepts the same block gets its weight added to *both* tallies.

### Finding Description
In `run` (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`), each `BlockResponse::Accepted` and `BlockResponse::Rejected` message updates a shared `BlockStatus` for the block's `signer_signature_hash`:

- `Rejected` branch: weight is added exactly once, guarded by `if block.responded_signers.insert(slot_id)` [1](#0-0) 
- `Accepted` branch: weight is added guarded only by `if !block.gathered_signatures.contains_key(&slot_id)`, and afterward it also inserts into `responded_signers` [2](#0-1) 

Both branches share the same `responded_signers: HashSet<u32>` (keyed by `slot_id`) and the same `blocks` map (guarded by one lock), so this is meant to be a single "has this signer already been counted" gate. But the `Accepted` branch checks a *different* set (`gathered_signatures`) instead of `responded_signers`.

Sequence that breaks the invariant:
1. Signer S (slot `k`, weight `w`) sends `Rejected` for block `B` first. `total_weight_rejected += w`; `responded_signers.insert(k)`.
2. S later sends `Accepted` for the same block `B` (e.g., after re-evaluating, or a delayed/duplicate/retried message arrives). `gathered_signatures` does not yet contain key `k` (it was never touched by the reject path), so the guard passes: `total_weight_approved += w` as well.

Result: `total_weight_approved + total_weight_rejected` now double-counts `w`, and `total_weight_approved` can cross `self.weight_threshold` using weight that is simultaneously recorded as a rejection. The equality that should hold — a signer's weight belongs to at most one of the two tallies — is broken by a single (minority) signer's message ordering, with no privileged action beyond a signer sending its own (legitimately signed) two different `BlockResponse` messages.

Before the attacker's action: `total_weight_approved` and `total_weight_rejected` are drawn from disjoint sets of `slot_id`s, so `approved + rejected <= total_weight`, and `weight_threshold` crossing on the approved side is a sound majority computation. After: a signer whose weight was already tallied on the reject side is silently added to the approve side too, so the node processing this event stream can conclude the acceptance/rejection threshold differently than another node (miner or signer coordinator) that consumed the same underlying messages in a different order, or dropped/deduped one of them differently.

### Impact Explanation
This is bounded to the miner/signer coordination layer (`stacks-node/src/nakamoto_node/signer_coordinator.rs` and `stackerdb_listener.rs`), which decides when a Nakamoto block is treated as locally accepted (enough approved weight) or as globally rejected (enough rejected weight, `total_weight_rejected + weight_threshold > total_weight`). Because the double count is order-dependent (depends on which message a given node processed first) and can be triggered by a single signer flipping its vote, different miners/coordinators can reach different local conclusions about the same block's status from the exact same underlying signer messages — a temporary tip/consensus-progress disagreement between nodes rather than a genuine 70% agreement. It does not directly forge a canonical, node-verified chain-state (that check ultimately still runs through `NakamotoBlockHeader::verify_signer_signatures`, which recomputes weight from actual signatures rather than trusting this in-memory tally), so it is a coordination-layer/liveness divergence rather than a state-root or double-payment bug.

### Likelihood Explanation
Any single registered signer can trigger this merely by sending two legitimate, correctly-signed `BlockResponse` messages (`Rejected` then `Accepted`) for the same block hash — no majority collusion, no privileged key beyond the signer's own, and no protocol violation is required to produce the two messages (retries, timeouts, or reconsideration under `handle_block_pre_commit`/`store_and_process_block_signature` logic could plausibly generate this ordering in practice). The bug is purely in local in-memory tallying inside `StackerDBListener`, so it is easily reachable whenever any one signer's vote flips relative to how a given node observed the message order.

### Recommendation
Gate the `Accepted` branch's weight increment on the same `responded_signers` set used by the `Rejected` branch (i.e., `if block.responded_signers.insert(slot_id) { total_weight_approved += weight; }`), so that each signer's weight can only ever land in one of `total_weight_approved` / `total_weight_rejected` for a given block, regardless of message arrival order or vote flips.

### Proof of Concept
1. Node receives `SignerMessageV0::BlockResponse(BlockResponse::Rejected(...))` for `block_sighash` from signer at `slot_id = k` with weight `w`. This inserts `k` into `responded_signers` and adds `w` to `total_weight_rejected` [1](#0-0) .
2. The same signer subsequently sends `SignerMessageV0::BlockResponse(BlockResponse::Accepted(...))` for the same `block_sighash`. Since `block.gathered_signatures` was never populated for `k`, the check `!block.gathered_signatures.contains_key(&slot_id)` is true, so `total_weight_approved` is incremented by `w` as well [2](#0-1) .
3. Now `total_weight_approved` includes `w` from a signer who is also counted in `total_weight_rejected`, so `get_block_status`/`SignerCoordinator` logic that checks `block_status.total_weight_approved >= self.weight_threshold` [3](#0-2)  can be satisfied using weight that should not have been available for the accept side, producing a threshold decision that another node (which saw the messages in the opposite order, or with only one of the two messages) would not reach.

### Citations

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-519)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
