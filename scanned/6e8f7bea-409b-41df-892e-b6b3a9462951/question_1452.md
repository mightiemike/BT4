# Q1452: sync-order dependent state via `get_l2_blocks_range` (l2.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling the timing of proof versus commitment arrival, drive `get_l2_blocks_range` in `crates/common/src/l2.rs` so that the state a node reaches syncing from genesis and the state a node reaches syncing from a snapshot stop being the same, breaking the invariant that final state is independent of sync path?

## Target
- File/function: `crates/common/src/l2.rs` -> `get_l2_blocks_range`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: sync-order dependent state - reach `get_l2_blocks_range` from that entrypoint and force the divergence where the state a node reaches syncing from genesis and the state a node reaches syncing from a snapshot stop being the same; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `execute_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: final state is independent of sync path
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: sync both ways over the same range and diff roots
