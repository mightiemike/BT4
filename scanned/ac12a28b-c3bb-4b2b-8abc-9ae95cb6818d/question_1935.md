# Q1935: proof-of-a-proof method id check via `insert` (accessors.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling which commitment indices are covered, drive `insert` in `crates/light-client-prover/src/circuit/accessors.rs` so that the method id used to verify a batch proof and the id authorised at that L2 height stop being the same, breaking the invariant that proofs are verified under the authorised circuit?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `insert`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: which commitment indices are covered
- Exploit idea: proof-of-a-proof method id check - reach `insert` from that entrypoint and force the divergence where the method id used to verify a batch proof and the id authorised at that L2 height stop being the same; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proofs are verified under the authorised circuit
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: verify a proof produced by a stale method id and assert rejection
