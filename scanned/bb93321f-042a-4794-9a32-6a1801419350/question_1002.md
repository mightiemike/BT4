# Q1002: L1 reorg handling via `extract_zk_proofs_and_sequencer_commitments` (da.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling the timing of proof versus commitment arrival, drive `extract_zk_proofs_and_sequencer_commitments` in `crates/common/src/da.rs` so that the L2 state a node holds after a reorg and the state implied by the canonical L1 chain stop being the same, breaking the invariant that reorg handling restores the canonical view?

## Target
- File/function: `crates/common/src/da.rs` -> `extract_zk_proofs_and_sequencer_commitments`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: L1 reorg handling - reach `extract_zk_proofs_and_sequencer_commitments` from that entrypoint and force the divergence where the L2 state a node holds after a reorg and the state implied by the canonical L1 chain stop being the same; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `sync_l1`, `get_da_block_at_height`, `extract_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: reorg handling restores the canonical view
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: reorg beneath processed commitments and assert convergence
