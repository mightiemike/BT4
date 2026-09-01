# Q0052: commitment overwrite on conflict via `extract_sequencer_commitments` (da.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling the timing of proof versus commitment arrival, drive `extract_sequencer_commitments` in `crates/common/src/da.rs` so that the commitment a node keeps for an index and the one Bitcoin finally confirms stop being the same, breaking the invariant that conflicting commitments resolve to the confirmed one?

## Target
- File/function: `crates/common/src/da.rs` -> `extract_sequencer_commitments`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: commitment overwrite on conflict - reach `extract_sequencer_commitments` from that entrypoint and force the divergence where the commitment a node keeps for an index and the one Bitcoin finally confirms stop being the same; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `sync_l1`, `get_da_block_at_height`, `extract_zk_proofs_and_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: conflicting commitments resolve to the confirmed one
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: publish conflicting commitments and assert resolution
