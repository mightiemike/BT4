# Q0050: blob size threshold split via `check_state_diff_threshold` (controller.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling transaction sizes at the blob threshold, drive `check_state_diff_threshold` in `crates/sequencer/src/commitment/controller.rs` so that the L2 blocks packed into a commitment blob and the blocks the commitment claims stop being the same set, breaking the invariant that chunking never changes commitment contents?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `check_state_diff_threshold`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: blob size threshold split - reach `check_state_diff_threshold` from that entrypoint and force the divergence where the L2 blocks packed into a commitment blob and the blocks the commitment claims stop being the same set; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_max_l2_blocks`, `next_commitment_start_height`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: chunking never changes commitment contents
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: produce a commitment at the size threshold and re-parse it
