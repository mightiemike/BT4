# Q0072: L1 reorg during commitment via `check_state_diff_threshold` (controller.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling transaction sizes at the blob threshold, drive `check_state_diff_threshold` in `crates/sequencer/src/commitment/controller.rs` so that the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same, breaking the invariant that commitments survive or are cleanly re-anchored across reorgs?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `check_state_diff_threshold`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: L1 reorg during commitment - reach `check_state_diff_threshold` from that entrypoint and force the divergence where the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_max_l2_blocks`, `next_commitment_start_height`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitments survive or are cleanly re-anchored across reorgs
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: reorg beneath a published commitment and assert recovery
