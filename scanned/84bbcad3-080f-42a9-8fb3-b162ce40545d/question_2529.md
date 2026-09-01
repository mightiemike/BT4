# Q2529: l2 sync trusting sequencer signature via `ProofError` (error.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling what a syncing node sees first, drive `ProofError` in `crates/fullnode/src/error.rs` so that the block a node accepts from the L2 sync path and the block covered by a signed commitment stop being the same block, breaking the invariant that synced blocks are covered by sequencer authority?

## Target
- File/function: `crates/fullnode/src/error.rs` -> `ProofError`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: what a syncing node sees first
- Exploit idea: l2 sync trusting sequencer signature - reach `ProofError` from that entrypoint and force the divergence where the block a node accepts from the L2 sync path and the block covered by a signed commitment stop being the same block; the adjacent symbols in the same file that carry the value are `CommitmentError`, `HaltingError`, `SkippableError`, `ProcessingError`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: synced blocks are covered by sequencer authority
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: serve an unsigned block over the sync path and assert rejection
