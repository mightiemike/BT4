# Q1312: adopting a root without its proof via `get_da_block_at_height` (da.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling what a syncing node sees first, drive `get_da_block_at_height` in `crates/common/src/da.rs` so that the root a node advertises as final and the root a verified proof commits stop being the same, breaking the invariant that finality requires a verified proof?

## Target
- File/function: `crates/common/src/da.rs` -> `get_da_block_at_height`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: what a syncing node sees first
- Exploit idea: adopting a root without its proof - reach `get_da_block_at_height` from that entrypoint and force the divergence where the root a node advertises as final and the root a verified proof commits stop being the same; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `sync_l1`, `extract_sequencer_commitments`, `extract_zk_proofs_and_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: finality requires a verified proof
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: advertise before verification and assert refusal
