# Q2963: L1 reorg during commitment via `next_commitment_start_height` (controller.rs)

## Question
Can an unprivileged attacker who sends transactions sized to sit exactly at the commitment blob size threshold, controlling reorg depth achievable with valid Bitcoin transactions, drive `next_commitment_start_height` in `crates/sequencer/src/commitment/controller.rs` so that the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same, breaking the invariant that commitments survive or are cleanly re-anchored across reorgs?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `next_commitment_start_height`
- Entrypoint: unprivileged party sends transactions sized to sit exactly at the commitment blob size threshold
- Attacker controls: reorg depth achievable with valid Bitcoin transactions
- Exploit idea: L1 reorg during commitment - reach `next_commitment_start_height` from that entrypoint and force the divergence where the L1 block a commitment was anchored to and the L1 block that ends up canonical stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_state_diff_threshold`, `check_max_l2_blocks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitments survive or are cleanly re-anchored across reorgs
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: reorg beneath a published commitment and assert recovery
