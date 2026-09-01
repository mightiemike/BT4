# Q2691: l2 sync trusting sequencer signature via `HaltingError` (error.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling the timing of proof versus commitment arrival, drive `HaltingError` in `crates/fullnode/src/error.rs` so that the block a node accepts from the L2 sync path and the block covered by a signed commitment stop being the same block, breaking the invariant that synced blocks are covered by sequencer authority?

## Target
- File/function: `crates/fullnode/src/error.rs` -> `HaltingError`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: l2 sync trusting sequencer signature - reach `HaltingError` from that entrypoint and force the divergence where the block a node accepts from the L2 sync path and the block covered by a signed commitment stop being the same block; the adjacent symbols in the same file that carry the value are `ProofError`, `CommitmentError`, `SkippableError`, `ProcessingError`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: synced blocks are covered by sequencer authority
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: serve an unsigned block over the sync path and assert rejection
