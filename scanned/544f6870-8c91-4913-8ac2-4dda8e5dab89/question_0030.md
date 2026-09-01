# Q0030: L1 reorg handling via `get_da_block_at_height` (da.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling what a syncing node sees first, drive `get_da_block_at_height` in `crates/common/src/da.rs` so that the L2 state a node holds after a reorg and the state implied by the canonical L1 chain stop being the same, breaking the invariant that reorg handling restores the canonical view?

## Target
- File/function: `crates/common/src/da.rs` -> `get_da_block_at_height`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: what a syncing node sees first
- Exploit idea: L1 reorg handling - reach `get_da_block_at_height` from that entrypoint and force the divergence where the L2 state a node holds after a reorg and the state implied by the canonical L1 chain stop being the same; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `sync_l1`, `extract_sequencer_commitments`, `extract_zk_proofs_and_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: reorg handling restores the canonical view
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: reorg beneath processed commitments and assert convergence
