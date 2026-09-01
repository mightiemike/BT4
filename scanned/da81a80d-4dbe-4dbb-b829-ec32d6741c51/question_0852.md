# Q0852: commitment overwrite on conflict via `process_zk_proof` (da_block_handler.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling what a syncing node sees first, drive `process_zk_proof` in `crates/fullnode/src/da_block_handler.rs` so that the commitment a node keeps for an index and the one Bitcoin finally confirms stop being the same, breaking the invariant that conflicting commitments resolve to the confirmed one?

## Target
- File/function: `crates/fullnode/src/da_block_handler.rs` -> `process_zk_proof`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: what a syncing node sees first
- Exploit idea: commitment overwrite on conflict - reach `process_zk_proof` from that entrypoint and force the divergence where the commitment a node keeps for an index and the one Bitcoin finally confirms stop being the same; the adjacent symbols in the same file that carry the value are `ProcessingResult`, `ProofSource`, `L1BlockHandler`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: conflicting commitments resolve to the confirmed one
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: publish conflicting commitments and assert resolution
