# Q4564: L1 reorg during commitment via `last_l2_height` (controller.rs)

## Question
Can an unprivileged attacker who submits transactions that land in the last L2 block before a sequencer commitment is produced, controlling transaction sizes at the blob threshold, drive `last_l2_height` in `crates/sequencer/src/commitment/controller.rs` so that the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same, breaking the invariant that commitments survive or are cleanly re-anchored across reorgs?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `last_l2_height`
- Entrypoint: unprivileged party submits transactions that land in the last L2 block before a sequencer commitment is produced
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: L1 reorg during commitment - reach `last_l2_height` from that entrypoint and force the divergence where the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_state_diff_threshold`, `check_max_l2_blocks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitments survive or are cleanly re-anchored across reorgs
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: reorg beneath a published commitment and assert recovery
