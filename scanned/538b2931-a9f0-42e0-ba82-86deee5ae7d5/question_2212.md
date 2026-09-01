# Q2212: L1 reorg handling via `process_zk_proof` (da_block_handler.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling conflicting L1 data across a reorg, drive `process_zk_proof` in `crates/fullnode/src/da_block_handler.rs` so that the L2 state a node holds after a reorg and the state implied by the canonical L1 chain stop being the same, breaking the invariant that reorg handling restores the canonical view?

## Target
- File/function: `crates/fullnode/src/da_block_handler.rs` -> `process_zk_proof`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: conflicting L1 data across a reorg
- Exploit idea: L1 reorg handling - reach `process_zk_proof` from that entrypoint and force the divergence where the L2 state a node holds after a reorg and the state implied by the canonical L1 chain stop being the same; the adjacent symbols in the same file that carry the value are `ProcessingResult`, `ProofSource`, `L1BlockHandler`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: reorg handling restores the canonical view
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: reorg beneath processed commitments and assert convergence
