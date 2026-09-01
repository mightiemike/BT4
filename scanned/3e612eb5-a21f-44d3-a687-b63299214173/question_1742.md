# Q1742: error path leaves partial state via `process_l2_block` (l2_syncer.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling conflicting L1 data across a reorg, drive `process_l2_block` in `crates/fullnode/src/l2_syncer.rs` so that the state after a failed apply and the state before it stop being the same, breaking the invariant that failed applies are atomic?

## Target
- File/function: `crates/fullnode/src/l2_syncer.rs` -> `process_l2_block`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: conflicting L1 data across a reorg
- Exploit idea: error path leaves partial state - reach `process_l2_block` from that entrypoint and force the divergence where the state after a failed apply and the state before it stop being the same; the adjacent symbols in the same file that carry the value are `L2Syncer`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: failed applies are atomic
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fail mid-apply and assert rollback
