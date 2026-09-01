# Q0094: pending commitment overwrite via `check_max_l2_blocks` (controller.rs)

## Question
Can an unprivileged attacker who submits transactions that land in the last L2 block before a sequencer commitment is produced, controlling transaction sizes at the blob threshold, drive `check_max_l2_blocks` in `crates/sequencer/src/commitment/controller.rs` so that the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object, breaking the invariant that stored commitments match published ones?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `check_max_l2_blocks`
- Entrypoint: unprivileged party submits transactions that land in the last L2 block before a sequencer commitment is produced
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: pending commitment overwrite - reach `check_max_l2_blocks` from that entrypoint and force the divergence where the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_state_diff_threshold`, `next_commitment_start_height`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: stored commitments match published ones
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: publish then overwrite and assert the stored value tracks Bitcoin
