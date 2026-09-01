# Q0202: adopting a root without its proof via `get_l2_blocks_range` (l2.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling the timing of proof versus commitment arrival, drive `get_l2_blocks_range` in `crates/common/src/l2.rs` so that the root a node advertises as final and the root a verified proof commits stop being the same, breaking the invariant that finality requires a verified proof?

## Target
- File/function: `crates/common/src/l2.rs` -> `get_l2_blocks_range`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: adopting a root without its proof - reach `get_l2_blocks_range` from that entrypoint and force the divergence where the root a node advertises as final and the root a verified proof commits stop being the same; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `execute_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: finality requires a verified proof
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: advertise before verification and assert refusal
