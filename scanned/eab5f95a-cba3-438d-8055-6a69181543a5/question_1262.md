# Q1262: proof-before-commitment ordering via `extract_zk_proofs_and_sequencer_commitments` (da.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling conflicting L1 data across a reorg, drive `extract_zk_proofs_and_sequencer_commitments` in `crates/common/src/da.rs` so that the state a node adopts when a proof arrives before its commitment and the state when it arrives after stop being the same, breaking the invariant that adoption is order-independent?

## Target
- File/function: `crates/common/src/da.rs` -> `extract_zk_proofs_and_sequencer_commitments`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: conflicting L1 data across a reorg
- Exploit idea: proof-before-commitment ordering - reach `extract_zk_proofs_and_sequencer_commitments` from that entrypoint and force the divergence where the state a node adopts when a proof arrives before its commitment and the state when it arrives after stop being the same; the adjacent symbols in the same file that carry the value are `ProofOrCommitment`, `sync_l1`, `get_da_block_at_height`, `extract_sequencer_commitments`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: adoption is order-independent
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: deliver in both orders and diff
