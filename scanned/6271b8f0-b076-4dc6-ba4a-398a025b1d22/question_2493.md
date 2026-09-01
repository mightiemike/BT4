# Q2493: proof-before-commitment ordering via `ProcessingError` (error.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling what a syncing node sees first, drive `ProcessingError` in `crates/fullnode/src/error.rs` so that the state a node adopts when a proof arrives before its commitment and the state when it arrives after stop being the same, breaking the invariant that adoption is order-independent?

## Target
- File/function: `crates/fullnode/src/error.rs` -> `ProcessingError`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: what a syncing node sees first
- Exploit idea: proof-before-commitment ordering - reach `ProcessingError` from that entrypoint and force the divergence where the state a node adopts when a proof arrives before its commitment and the state when it arrives after stop being the same; the adjacent symbols in the same file that carry the value are `ProofError`, `CommitmentError`, `HaltingError`, `SkippableError`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: adoption is order-independent
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: deliver in both orders and diff
