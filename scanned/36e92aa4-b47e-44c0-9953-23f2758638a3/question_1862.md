# Q1862: pending proof handling on restart via `sync_l1` (da.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling the timing of proof versus commitment arrival, drive `sync_l1` in `crates/common/src/da.rs` so that the proof set a node holds before restart and the set after stop being the same, breaking the invariant that restart preserves exactly the verified set?

## Target
- File/function: `crates/common/src/da.rs` -> `sync_l1`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: pending proof handling on restart - reach `sync_l1` from that entrypoint and force the divergence where the proof set a node holds before restart and the set after stop being the same; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `get_da_block_at_height`, `extract_sequencer_commitments`, `extract_zk_proofs_and_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: restart preserves exactly the verified set
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: restart mid-verification and diff
