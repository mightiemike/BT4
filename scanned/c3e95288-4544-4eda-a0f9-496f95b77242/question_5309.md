# Q5309: commitment merkle leaf ordering via `check_max_l2_blocks` (controller.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling transaction sizes at the blob threshold, drive `check_max_l2_blocks` in `crates/sequencer/src/commitment/controller.rs` so that the leaf order the commitment root was built from and the order a verifier reconstructs stop being the same, breaking the invariant that commitment roots are order-canonical?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `check_max_l2_blocks`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: commitment merkle leaf ordering - reach `check_max_l2_blocks` from that entrypoint and force the divergence where the leaf order the commitment root was built from and the order a verifier reconstructs stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_state_diff_threshold`, `next_commitment_start_height`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitment roots are order-canonical
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: reconstruct the root from stored blocks and compare
