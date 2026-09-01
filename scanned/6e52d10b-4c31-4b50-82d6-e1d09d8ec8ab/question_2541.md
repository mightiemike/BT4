# Q2541: chunk wtxid squatting via `insert` (accessors.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::Chunk` blob from an unknown key (no sender check exists on that path), controlling the order in which chunks land in the block, drive `insert` in `crates/light-client-prover/src/circuit/accessors.rs` so that the wtxid an honest aggregate lists and the wtxid whose body the attacker planted stop resolving to different bodies, breaking the invariant that an aggregate resolves only to chunks its author produced?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `insert`
- Entrypoint: unprivileged party inscribes a `DataOnDa::Chunk` blob from an unknown key (no sender check exists on that path)
- Attacker controls: the order in which chunks land in the block
- Exploit idea: chunk wtxid squatting - reach `insert` from that entrypoint and force the divergence where the wtxid an honest aggregate lists and the wtxid whose body the attacker planted stop resolving to different bodies; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an aggregate resolves only to chunks its author produced
- Expected Immunefi impact: Critical - light client proof split: two honest provers commit different outputs for the same L1 block, attacking Clementine operators
- Fast validation: plant a chunk under a referenced wtxid and assert the aggregate is refused
