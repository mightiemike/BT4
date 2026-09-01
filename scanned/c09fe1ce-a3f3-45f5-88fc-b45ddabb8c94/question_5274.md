# Q5274: pending commitment overwrite via `next_commitment_start_height` (controller.rs)

## Question
Can an unprivileged attacker who broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to, controlling reorg depth achievable with valid Bitcoin transactions, drive `next_commitment_start_height` in `crates/sequencer/src/commitment/controller.rs` so that the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object, breaking the invariant that stored commitments match published ones?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `next_commitment_start_height`
- Entrypoint: unprivileged party broadcasts valid Bitcoin transactions that reorg the L1 block a commitment was anchored to
- Attacker controls: reorg depth achievable with valid Bitcoin transactions
- Exploit idea: pending commitment overwrite - reach `next_commitment_start_height` from that entrypoint and force the divergence where the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object; the adjacent symbols in the same file that carry the value are `CommitmentController`, `should_commit`, `check_state_diff_threshold`, `check_max_l2_blocks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: stored commitments match published ones
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: publish then overwrite and assert the stored value tracks Bitcoin
