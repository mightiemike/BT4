# Q1052: pending proof handling on restart via `commit_l2_block` (l2.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling what a syncing node sees first, drive `commit_l2_block` in `crates/common/src/l2.rs` so that the proof set a node holds before restart and the set after stop being the same, breaking the invariant that restart preserves exactly the verified set?

## Target
- File/function: `crates/common/src/l2.rs` -> `commit_l2_block`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: what a syncing node sees first
- Exploit idea: pending proof handling on restart - reach `commit_l2_block` from that entrypoint and force the divergence where the proof set a node holds before restart and the set after stop being the same; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `execute_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart preserves exactly the verified set
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: restart mid-verification and diff
