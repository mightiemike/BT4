# Q0138: commitment merkle leaf ordering via `next_commitment_start_height` (controller.rs)

## Question
Can an unprivileged attacker who submits transactions that land in the last L2 block before a sequencer commitment is produced, controlling reorg depth achievable with valid Bitcoin transactions, drive `next_commitment_start_height` in `crates/sequencer/src/commitment/controller.rs` so that the leaf order the commitment root was built from and the order a verifier reconstructs stop being the same, breaking the invariant that commitment roots are order-canonical?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `next_commitment_start_height`
- Entrypoint: unprivileged party submits transactions that land in the last L2 block before a sequencer commitment is produced
- Attacker controls: reorg depth achievable with valid Bitcoin transactions
- Exploit idea: commitment merkle leaf ordering - reach `next_commitment_start_height` from that entrypoint and force the divergence where the leaf order the commitment root was built from and the order a verifier reconstructs stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_state_diff_threshold`, `check_max_l2_blocks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitment roots are order-canonical
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: reconstruct the root from stored blocks and compare
