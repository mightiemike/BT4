# Q0442: da handler height bookkeeping via `ProofError` (error.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling the timing of proof versus commitment arrival, drive `ProofError` in `crates/fullnode/src/error.rs` so that the L1 height a node believes processed and the height it actually applied stop being equal, breaking the invariant that processed-height bookkeeping is exact?

## Target
- File/function: `crates/fullnode/src/error.rs` -> `ProofError`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: da handler height bookkeeping - reach `ProofError` from that entrypoint and force the divergence where the L1 height a node believes processed and the height it actually applied stop being equal; the adjacent symbols in the same file that carry the value are `CommitmentError`, `HaltingError`, `SkippableError`, `ProcessingError`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: processed-height bookkeeping is exact
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: crash between apply and record, restart, and diff
