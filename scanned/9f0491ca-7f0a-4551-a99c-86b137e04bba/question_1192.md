# Q1192: proof-before-commitment ordering via `ProofError` (error.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling the timing of proof versus commitment arrival, drive `ProofError` in `crates/fullnode/src/error.rs` so that the state a node adopts when a proof arrives before its commitment and the state when it arrives after stop being the same, breaking the invariant that adoption is order-independent?

## Target
- File/function: `crates/fullnode/src/error.rs` -> `ProofError`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: proof-before-commitment ordering - reach `ProofError` from that entrypoint and force the divergence where the state a node adopts when a proof arrives before its commitment and the state when it arrives after stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentError`, `HaltingError`, `SkippableError`, `ProcessingError`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: adoption is order-independent
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: deliver in both orders and diff
