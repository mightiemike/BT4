# Q2769: adopting a root without its proof via `process_l2_block` (l2_syncer.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling the timing of proof versus commitment arrival, drive `process_l2_block` in `crates/fullnode/src/l2_syncer.rs` so that the root a node advertises as final and the root a verified proof commits stop being the same, breaking the invariant that finality requires a verified proof?

## Target
- File/function: `crates/fullnode/src/l2_syncer.rs` -> `process_l2_block`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: adopting a root without its proof - reach `process_l2_block` from that entrypoint and force the divergence where the root a node advertises as final and the root a verified proof commits stop being the same; the adjacent symbols in the same file that carry the value are `L2Syncer`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: finality requires a verified proof
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: advertise before verification and assert refusal
