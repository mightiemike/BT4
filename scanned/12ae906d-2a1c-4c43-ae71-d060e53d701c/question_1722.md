# Q1722: pending proof handling on restart via `SkippableError` (error.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling the timing of proof versus commitment arrival, drive `SkippableError` in `crates/fullnode/src/error.rs` so that the proof set a node holds before restart and the set after stop being the same, breaking the invariant that restart preserves exactly the verified set?

## Target
- File/function: `crates/fullnode/src/error.rs` -> `SkippableError`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: pending proof handling on restart - reach `SkippableError` from that entrypoint and force the divergence where the proof set a node holds before restart and the set after stop being the same; the adjacent symbols in the same file that carry the value are `ProofError`, `CommitmentError`, `HaltingError`, `ProcessingError`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart preserves exactly the verified set
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: restart mid-verification and diff
