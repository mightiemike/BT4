# Q2700: adopting a root without its proof via `HaltingError` (error.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling the timing of proof versus commitment arrival, drive `HaltingError` in `crates/fullnode/src/error.rs` so that the root a node advertises as final and the root a verified proof commits stop being the same, breaking the invariant that finality requires a verified proof?

## Target
- File/function: `crates/fullnode/src/error.rs` -> `HaltingError`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: adopting a root without its proof - reach `HaltingError` from that entrypoint and force the divergence where the root a node advertises as final and the root a verified proof commits stop being the same; the adjacent symbols in the same file that carry the value are `ProofError`, `CommitmentError`, `SkippableError`, `ProcessingError`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: finality requires a verified proof
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: advertise before verification and assert refusal
