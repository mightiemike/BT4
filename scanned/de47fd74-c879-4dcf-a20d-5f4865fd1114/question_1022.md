# Q1022: commitment overwrite on conflict via `execute_l2_block` (l2.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling conflicting L1 data across a reorg, drive `execute_l2_block` in `crates/common/src/l2.rs` so that the commitment a node keeps for an index and the one Bitcoin finally confirms stop being the same, breaking the invariant that conflicting commitments resolve to the confirmed one?

## Target
- File/function: `crates/common/src/l2.rs` -> `execute_l2_block`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: conflicting L1 data across a reorg
- Exploit idea: commitment overwrite on conflict - reach `execute_l2_block` from that entrypoint and force the divergence where the commitment a node keeps for an index and the one Bitcoin finally confirms stop being the same; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `commit_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: conflicting commitments resolve to the confirmed one
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: publish conflicting commitments and assert resolution
