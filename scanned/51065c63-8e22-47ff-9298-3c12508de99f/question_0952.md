# Q0952: commitment overwrite on conflict via `run` (l2_syncer.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling what a syncing node sees first, drive `run` in `crates/fullnode/src/l2_syncer.rs` so that the commitment a node keeps for an index and the one Bitcoin finally confirms stop being the same, breaking the invariant that conflicting commitments resolve to the confirmed one?

## Target
- File/function: `crates/fullnode/src/l2_syncer.rs` -> `run`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: what a syncing node sees first
- Exploit idea: commitment overwrite on conflict - reach `run` from that entrypoint and force the divergence where the commitment a node keeps for an index and the one Bitcoin finally confirms stop being the same; the adjacent symbols in the same file that carry the value are `L2Syncer`, `process_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: conflicting commitments resolve to the confirmed one
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: publish conflicting commitments and assert resolution
