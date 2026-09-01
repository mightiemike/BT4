# Q4529: short header proof requested versus stored via `should_commit` (controller.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling transaction sizes at the blob threshold, drive `should_commit` in `crates/sequencer/src/commitment/controller.rs` so that the short header proof the sequencer stored for an L1 hash and the proof the prover later needs stop being the same artefact, breaking the invariant that every queried L1 hash has a matching stored proof?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `should_commit`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: short header proof requested versus stored - reach `should_commit` from that entrypoint and force the divergence where the short header proof the sequencer stored for an L1 hash and the proof the prover later needs stop being the same artefact; the adjacent symbols in the same file that carry the value are `CommitmentController`, `check_state_diff_threshold`, `check_max_l2_blocks`, `next_commitment_start_height`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every queried L1 hash has a matching stored proof
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: query an unstored hash and assert a defined outcome
