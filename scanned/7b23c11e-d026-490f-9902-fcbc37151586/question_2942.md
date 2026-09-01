# Q2942: commitment index continuity via `check_max_l2_blocks` (controller.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling the L2 height at which its transactions land, drive `check_max_l2_blocks` in `crates/sequencer/src/commitment/controller.rs` so that the commitment index the sequencer emits and the index the light client expects next stop being consecutive, breaking the invariant that commitment indices form a gapless chain?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `check_max_l2_blocks`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: commitment index continuity - reach `check_max_l2_blocks` from that entrypoint and force the divergence where the commitment index the sequencer emits and the index the light client expects next stop being consecutive; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_state_diff_threshold`, `next_commitment_start_height`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitment indices form a gapless chain
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: emit around a gap and assert the light client refuses to advance
