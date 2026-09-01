# Q2012: L1 reorg handling via `get_l2_blocks_range` (l2.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling what a syncing node sees first, drive `get_l2_blocks_range` in `crates/common/src/l2.rs` so that the L2 state a node holds after a reorg and the state implied by the canonical L1 chain stop being the same, breaking the invariant that reorg handling restores the canonical view?

## Target
- File/function: `crates/common/src/l2.rs` -> `get_l2_blocks_range`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: what a syncing node sees first
- Exploit idea: L1 reorg handling - reach `get_l2_blocks_range` from that entrypoint and force the divergence where the L2 state a node holds after a reorg and the state implied by the canonical L1 chain stop being the same; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `execute_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: reorg handling restores the canonical view
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: reorg beneath processed commitments and assert convergence
