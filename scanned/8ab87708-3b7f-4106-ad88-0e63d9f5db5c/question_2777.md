# Q2777: pending proof handling on restart via `process_l2_block` (l2_syncer.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling what a syncing node sees first, drive `process_l2_block` in `crates/fullnode/src/l2_syncer.rs` so that the proof set a node holds before restart and the set after stop being the same, breaking the invariant that restart preserves exactly the verified set?

## Target
- File/function: `crates/fullnode/src/l2_syncer.rs` -> `process_l2_block`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: what a syncing node sees first
- Exploit idea: pending proof handling on restart - reach `process_l2_block` from that entrypoint and force the divergence where the proof set a node holds before restart and the set after stop being the same; the adjacent symbols in the same file that carry the value are `L2Syncer`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart preserves exactly the verified set
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: restart mid-verification and diff
