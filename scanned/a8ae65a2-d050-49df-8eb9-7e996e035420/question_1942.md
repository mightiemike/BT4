# Q1942: l2 sync trusting sequencer signature via `extract_zk_proofs_and_sequencer_commitments` (da.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling what a syncing node sees first, drive `extract_zk_proofs_and_sequencer_commitments` in `crates/common/src/da.rs` so that the block a node accepts from the L2 sync path and the block covered by a signed commitment stop being the same block, breaking the invariant that synced blocks are covered by sequencer authority?

## Target
- File/function: `crates/common/src/da.rs` -> `extract_zk_proofs_and_sequencer_commitments`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: what a syncing node sees first
- Exploit idea: l2 sync trusting sequencer signature - reach `extract_zk_proofs_and_sequencer_commitments` from that entrypoint and force the divergence where the block a node accepts from the L2 sync path and the block covered by a signed commitment stop being the same block; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `sync_l1`, `get_da_block_at_height`, `extract_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: synced blocks are covered by sequencer authority
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: serve an unsigned block over the sync path and assert rejection
