# Q0180: commitment range off-by-one via `next_commitment_start_height` (controller.rs)

## Question
Can an unprivileged attacker who submits transactions that land in the last L2 block before a sequencer commitment is produced, controlling transaction sizes at the blob threshold, drive `next_commitment_start_height` in `crates/sequencer/src/commitment/controller.rs` so that the L2 height range a sequencer commitment covers and the range its merkle root was built from stop being the same range, breaking the invariant that a commitment's root commits exactly its declared range?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `next_commitment_start_height`
- Entrypoint: unprivileged party submits transactions that land in the last L2 block before a sequencer commitment is produced
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: commitment range off-by-one - reach `next_commitment_start_height` from that entrypoint and force the divergence where the L2 height range a sequencer commitment covers and the range its merkle root was built from stop being the same range; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_state_diff_threshold`, `check_max_l2_blocks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a commitment's root commits exactly its declared range
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: build a commitment at a range edge and re-derive its root
