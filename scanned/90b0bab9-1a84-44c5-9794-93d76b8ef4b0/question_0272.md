# Q0272: unverified data persisted as canonical via `process_l1_block` (da_block_handler.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling the timing of proof versus commitment arrival, drive `process_l1_block` in `crates/fullnode/src/da_block_handler.rs` so that the data a node persists and the data it has verified stop being the same set, breaking the invariant that only verified data becomes canonical?

## Target
- File/function: `crates/fullnode/src/da_block_handler.rs` -> `process_l1_block`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: unverified data persisted as canonical - reach `process_l1_block` from that entrypoint and force the divergence where the data a node persists and the data it has verified stop being the same set; the adjacent symbols in the same file that carry the value are `ProcessingResult`, `ProofSource`, `L1BlockHandler`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only verified data becomes canonical
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: persist a pending proof and assert it is not served as final
