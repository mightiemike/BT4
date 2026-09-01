# Q1642: error path leaves partial state via `HaltingError` (error.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling conflicting L1 data across a reorg, drive `HaltingError` in `crates/fullnode/src/error.rs` so that the state after a failed apply and the state before it stop being the same, breaking the invariant that failed applies are atomic?

## Target
- File/function: `crates/fullnode/src/error.rs` -> `HaltingError`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: conflicting L1 data across a reorg
- Exploit idea: error path leaves partial state - reach `HaltingError` from that entrypoint and force the divergence where the state after a failed apply and the state before it stop being the same; the adjacent symbols in the same file that carry the value are `ProofError`, `CommitmentError`, `SkippableError`, `ProcessingError`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: failed applies are atomic
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fail mid-apply and assert rollback
