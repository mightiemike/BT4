# Q0144: prior-output carry-over via `key` (accessors.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling which commitment indices are covered, drive `key` in `crates/light-client-prover/src/circuit/accessors.rs` so that the previous output the circuit assumes and the previous output actually produced stop being the same journal, breaking the invariant that each proof chains to its true predecessor?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `key`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: which commitment indices are covered
- Exploit idea: prior-output carry-over - reach `key` from that entrypoint and force the divergence where the previous output the circuit assumes and the previous output actually produced stop being the same journal; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each proof chains to its true predecessor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed a mismatched previous output and assert rejection
