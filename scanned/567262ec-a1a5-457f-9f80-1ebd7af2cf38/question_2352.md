# Q2352: adopting a root without its proof via `process_l1_block` (da_block_handler.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling the timing of proof versus commitment arrival, drive `process_l1_block` in `crates/fullnode/src/da_block_handler.rs` so that the root a node advertises as final and the root a verified proof commits stop being the same, breaking the invariant that finality requires a verified proof?

## Target
- File/function: `crates/fullnode/src/da_block_handler.rs` -> `process_l1_block`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: adopting a root without its proof - reach `process_l1_block` from that entrypoint and force the divergence where the root a node advertises as final and the root a verified proof commits stop being the same; the adjacent symbols in the same file that carry the value are `ProcessingResult`, `ProofSource`, `L1BlockHandler`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: finality requires a verified proof
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: advertise before verification and assert refusal
