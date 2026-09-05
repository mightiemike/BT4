## Title
Signer's stale rejection weight is never retracted when the same signer later accepts, letting a minority permanently inflate `total_weight_rejected` — ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`StackerDBListener` tracks two independent weight tallies, `total_weight_approved` and `total_weight_rejected`, per block, gated by two *different* guard conditions on overlapping state — exactly the pattern in the reference report where `decrementGaugeBoost` and `decrementGaugesBoostIndexed` use inconsistent guards on the same `userGaugeBoost`/`_userGauges` state. Here, the `Accepted` branch guards its weight addition on `gathered_signatures.contains_key(&slot_id)`, while the `Rejected` branch guards on `responded_signers.insert(slot_id)` — but `responded_signers` is shared and unconditionally updated by the `Accepted` branch too. As a result, a signer who first rejects and later accepts the same block has their weight counted in `total_weight_rejected` permanently, with no corresponding retraction, while also being correctly added to `total_weight_approved`.

### Finding Description
In the `Accepted` arm, weight is added only if the slot hasn't previously signed, and `responded_signers` is also inserted unconditionally: [1](#0-0) 

In the `Rejected` arm, weight is added only if `responded_signers.insert(slot_id)` returns `true` (i.e., first time this slot appears in `responded_signers` at all, across both accept and reject events): [2](#0-1) 

Because `responded_signers` is a single shared set touched by both arms, the two branches implement inconsistent semantics for the same underlying "has this signer already voted" state, mirroring the ERC20Boost inconsistency where `decrementGaugeBoost` and `decrementGaugesBoostIndexed` used different conditions (`boost >= gaugeState.userGaugeBoost` vs. `_deprecatedGauges.contains(gauge) || boost >= gaugeState.userGaugeBoost`) on the same `getUserGaugeBoost` state without properly clearing/retracting the stale portion.

Concretely:
1. Signer S (weight `w`) sends `Rejected` first → `responded_signers.insert(slot)` returns `true` → `total_weight_rejected += w`.
2. Signer S later sends `Accepted` for the same block → `gathered_signatures.contains_key(slot)` is `false` (S never signed before) → `total_weight_approved += w` (correct, this is genuinely a new signature) — but `total_weight_rejected` is *never* decremented, and there is no code path that removes `w` from `total_weight_rejected` once S switches its vote.

This is analogous to Alice's deprecated-gauge scenario: a piece of per-entity accounting state (`gaugeState.userGaugeBoost` there, `total_weight_rejected` here) can be repeatedly/partially manipulated through one code path (`decrementGaugeBoost` / `Rejected` arm) without the cleanup the other, "authoritative" code path (`decrementGaugesBoostIndexed` / the `Accepted` arm's `responded_signers` bookkeeping) is supposed to enforce.

### Impact Explanation
The coordinator's reject decision is driven purely by `total_weight_rejected`: [3](#0-2) 

Because stale rejection weight from signers who have since switched to accepting is never purged, a set of signers whose *current* combined rejecting weight is below the real blocking-minority threshold (`total_weight - weight_threshold`) can still trip the reject branch, purely due to residue from earlier votes that have since been retracted by the signer themselves. This is minority-triggerable (a single signer or small group flipping Reject→Accept is enough to leave permanent phantom weight) and causes the mining node to spuriously conclude "signers rejected" for a block that current signer opinion would actually approve, causing the coordinator to abort/exclude txids or give up the tenure — a locally-triggered, incorrect verdict on signer consensus.

### Likelihood Explanation
Any signer can trigger this simply by sending two ordinary, valid protocol messages (`Rejected` then `Accepted`) for the same `signer_signature_hash`; no privilege or majority coordination is required, only ordinary signer participation, matching the "signer weight below threshold" minority-triggerable class.

### Recommendation
Use disjoint/consistent bookkeeping: track a per-slot "last decision" (Accept/Reject) and, on a vote change, retract the previous contribution from the opposite tally before adding to the new one — i.e., make the `Rejected` arm's guard and cleanup consistent with the `Accepted` arm's `gathered_signatures`-based tracking, the same fix direction the original report recommended (align both code paths' conditions/cleanup on the same underlying per-entity state, e.g. keyed by slot_id membership in `gathered_signatures` vs. a separate `rejected_signers` map instead of a merged `responded_signers`).

### Proof of Concept
1. Miner requests signatures for block B; `BlockStatus` initialized with empty `responded_signers`, `total_weight_rejected = 0`.
2. Signer S (weight `w`, e.g., 20% of total, below the 30% blocking minority) sends `BlockResponse::Rejected` for B → `responded_signers.insert(slot_S)` → `total_weight_rejected = w`.
3. S reconsiders and sends `BlockResponse::Accepted` for B → `gathered_signatures` doesn't yet contain `slot_S` → `total_weight_approved += w` (correct) and `gathered_signatures/responded_signers` updated — but `total_weight_rejected` still equals `w`.
4. Other signers, whose combined weight is `blocking_minority - w` (i.e., together with S's stale rejection they cross `total_weight - weight_threshold`), send genuine `Rejected`.
5. `block.total_weight_rejected.saturating_add(weight_threshold) > total_weight` becomes true purely because of S's stale, already-retracted rejection weight, even though S itself currently supports the block — triggering `NakamotoNodeError::SignersRejected` despite the *real* current rejecting weight being below the blocking minority. [3](#0-2)

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L509-519)
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
```
