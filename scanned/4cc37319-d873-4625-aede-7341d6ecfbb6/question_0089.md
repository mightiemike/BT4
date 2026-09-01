# Q0089: commitment stored once semantics via `insert` (accessors.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling the chunk/aggregate graph it plants, drive `insert` in `crates/light-client-prover/src/circuit/accessors.rs` so that the commitment stored for an index and the first commitment seen for that index stop being the same object, breaking the invariant that the first valid commitment per index wins, deterministically?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `insert`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: the chunk/aggregate graph it plants
- Exploit idea: commitment stored once semantics - reach `insert` from that entrypoint and force the divergence where the commitment stored for an index and the first commitment seen for that index stop being the same object; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the first valid commitment per index wins, deterministically
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe two commitments for one index and assert stable selection
