# Q0118: error path leaves partial state via `apply_l2_block` (l2.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling what a syncing node sees first, drive `apply_l2_block` in `crates/common/src/l2.rs` so that the state after a failed apply and the state before it stop being the same, breaking the invariant that failed applies are atomic?

## Target
- File/function: `crates/common/src/l2.rs` -> `apply_l2_block`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: what a syncing node sees first
- Exploit idea: error path leaves partial state - reach `apply_l2_block` from that entrypoint and force the divergence where the state after a failed apply and the state before it stop being the same; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `execute_l2_block`, `commit_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: failed applies are atomic
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fail mid-apply and assert rollback
