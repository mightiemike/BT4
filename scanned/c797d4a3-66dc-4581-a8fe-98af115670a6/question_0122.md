# Q0122: l2 height monotonicity via `get` (accessors.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling the chunk/aggregate graph it plants, drive `get` in `crates/light-client-prover/src/circuit/accessors.rs` so that the `last_l2_height` the output advertises and the height the accepted proofs actually cover stop being equal, breaking the invariant that advertised height equals proved height?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `get`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: the chunk/aggregate graph it plants
- Exploit idea: l2 height monotonicity - reach `get` from that entrypoint and force the divergence where the `last_l2_height` the output advertises and the height the accepted proofs actually cover stop being equal; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: advertised height equals proved height
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: accept a partial chain and check the advertised height
