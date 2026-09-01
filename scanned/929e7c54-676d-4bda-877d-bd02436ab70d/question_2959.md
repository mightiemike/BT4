# Q2959: chunk store poisoning across blocks via `key` (accessors.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::Chunk` blob from an unknown key (no sender check exists on that path), controlling chunk wtxids and their contents, drive `key` in `crates/light-client-prover/src/circuit/accessors.rs` so that the chunk store contents an honest prover starts an L1 block with and the contents another honest prover starts with stop being the same, breaking the invariant that chunk-store state is a deterministic function of processed L1 blocks?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `key`
- Entrypoint: unprivileged party inscribes a `DataOnDa::Chunk` blob from an unknown key (no sender check exists on that path)
- Attacker controls: chunk wtxids and their contents
- Exploit idea: chunk store poisoning across blocks - reach `key` from that entrypoint and force the divergence where the chunk store contents an honest prover starts an L1 block with and the contents another honest prover starts with stop being the same; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: chunk-store state is a deterministic function of processed L1 blocks
- Expected Immunefi impact: Critical - light client proof split: two honest provers commit different outputs for the same L1 block, attacking Clementine operators
- Fast validation: process the same L1 range in two nodes and diff the accessor state
