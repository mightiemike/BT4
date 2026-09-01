# Q0452: error path leaves partial state via `ProofError` (error.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling the timing of proof versus commitment arrival, drive `ProofError` in `crates/fullnode/src/error.rs` so that the state after a failed apply and the state before it stop being the same, breaking the invariant that failed applies are atomic?

## Target
- File/function: `crates/fullnode/src/error.rs` -> `ProofError`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: error path leaves partial state - reach `ProofError` from that entrypoint and force the divergence where the state after a failed apply and the state before it stop being the same; the adjacent symbols in the same file that carry the value are `CommitmentError`, `HaltingError`, `SkippableError`, `ProcessingError`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: failed applies are atomic
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fail mid-apply and assert rollback
