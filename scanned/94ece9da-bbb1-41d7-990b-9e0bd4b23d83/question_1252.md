# Q1252: sync-order dependent state via `extract_zk_proofs_and_sequencer_commitments` (da.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling the timing of proof versus commitment arrival, drive `extract_zk_proofs_and_sequencer_commitments` in `crates/common/src/da.rs` so that the state a node reaches syncing from genesis and the state a node reaches syncing from a snapshot stop being the same, breaking the invariant that final state is independent of sync path?

## Target
- File/function: `crates/common/src/da.rs` -> `extract_zk_proofs_and_sequencer_commitments`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: sync-order dependent state - reach `extract_zk_proofs_and_sequencer_commitments` from that entrypoint and force the divergence where the state a node reaches syncing from genesis and the state a node reaches syncing from a snapshot stop being the same; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `sync_l1`, `get_da_block_at_height`, `extract_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: final state is independent of sync path
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: sync both ways over the same range and diff roots
