# Q0008: sync-order dependent state via `sync_l1` (da.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling conflicting L1 data across a reorg, drive `sync_l1` in `crates/common/src/da.rs` so that the state a node reaches syncing from genesis and the state a node reaches syncing from a snapshot stop being the same, breaking the invariant that final state is independent of sync path?

## Target
- File/function: `crates/common/src/da.rs` -> `sync_l1`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: conflicting L1 data across a reorg
- Exploit idea: sync-order dependent state - reach `sync_l1` from that entrypoint and force the divergence where the state a node reaches syncing from genesis and the state a node reaches syncing from a snapshot stop being the same; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `get_da_block_at_height`, `extract_sequencer_commitments`, `extract_zk_proofs_and_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: final state is independent of sync path
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: sync both ways over the same range and diff roots
