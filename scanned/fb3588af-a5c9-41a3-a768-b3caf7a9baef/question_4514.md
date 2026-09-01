# Q4514: pending commitment overwrite via `should_commit` (controller.rs)

## Question
Can an unprivileged attacker who sends transactions sized to sit exactly at the commitment blob size threshold, controlling reorg depth achievable with valid Bitcoin transactions, drive `should_commit` in `crates/sequencer/src/commitment/controller.rs` so that the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object, breaking the invariant that stored commitments match published ones?

## Target
- File/function: `crates/sequencer/src/commitment/controller.rs` -> `should_commit`
- Entrypoint: unprivileged party sends transactions sized to sit exactly at the commitment blob size threshold
- Attacker controls: reorg depth achievable with valid Bitcoin transactions
- Exploit idea: pending commitment overwrite - reach `should_commit` from that entrypoint and force the divergence where the commitment stored for an index and the commitment actually published to Bitcoin stop being the same object; the adjacent symbols in the same file that carry the value are `CommitmentController`, `check_state_diff_threshold`, `check_max_l2_blocks`, `next_commitment_start_height`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: stored commitments match published ones
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: publish then overwrite and assert the stored value tracks Bitcoin
