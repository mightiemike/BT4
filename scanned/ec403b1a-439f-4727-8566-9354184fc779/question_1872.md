# Q1872: da handler height bookkeeping via `sync_l1` (da.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling what a syncing node sees first, drive `sync_l1` in `crates/common/src/da.rs` so that the L1 height a node believes processed and the height it actually applied stop being equal, breaking the invariant that processed-height bookkeeping is exact?

## Target
- File/function: `crates/common/src/da.rs` -> `sync_l1`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: what a syncing node sees first
- Exploit idea: da handler height bookkeeping - reach `sync_l1` from that entrypoint and force the divergence where the L1 height a node believes processed and the height it actually applied stop being equal; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `get_da_block_at_height`, `extract_sequencer_commitments`, `extract_zk_proofs_and_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: processed-height bookkeeping is exact
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: crash between apply and record, restart, and diff
