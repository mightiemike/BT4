# Q0735: commitment stored once semantics via `exists` (accessors.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling which commitment indices are covered, drive `exists` in `crates/light-client-prover/src/circuit/accessors.rs` so that the commitment stored for an index and the first commitment seen for that index stop being the same object, breaking the invariant that the first valid commitment per index wins, deterministically?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `exists`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: which commitment indices are covered
- Exploit idea: commitment stored once semantics - reach `exists` from that entrypoint and force the divergence where the commitment stored for an index and the first commitment seen for that index stop being the same object; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the first valid commitment per index wins, deterministically
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe two commitments for one index and assert stable selection
