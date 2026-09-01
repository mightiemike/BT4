# Q2973: commitment index gap resolution via `key` (accessors.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling which commitment indices are covered, drive `key` in `crates/light-client-prover/src/circuit/accessors.rs` so that the index the circuit advances to and the highest index with a continuous verified chain stop being equal, breaking the invariant that advancement requires an unbroken verified chain?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `key`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: which commitment indices are covered
- Exploit idea: commitment index gap resolution - reach `key` from that entrypoint and force the divergence where the index the circuit advances to and the highest index with a continuous verified chain stop being equal; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: advancement requires an unbroken verified chain
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: supply 3-4-5 and 7-8 and assert the advance stops at 5
