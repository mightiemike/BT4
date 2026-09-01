# Q2105: circuit input wiring via `key` (accessors.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling the initial and final roots the offered data claims, drive `key` in `crates/light-client-prover/src/circuit/accessors.rs` so that the inputs the service hands the circuit and the inputs derived from verified DA data stop being the same, breaking the invariant that circuit inputs come only from verified DA data?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `key`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: circuit input wiring - reach `key` from that entrypoint and force the divergence where the inputs the service hands the circuit and the inputs derived from verified DA data stop being the same; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: circuit inputs come only from verified DA data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: inject an unverified field and assert the circuit refuses
