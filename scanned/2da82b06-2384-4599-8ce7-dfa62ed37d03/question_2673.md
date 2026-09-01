# Q2673: unverified data persisted as canonical via `process_l2_block` (l2_syncer.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling what a syncing node sees first, drive `process_l2_block` in `crates/fullnode/src/l2_syncer.rs` so that the data a node persists and the data it has verified stop being the same set, breaking the invariant that only verified data becomes canonical?

## Target
- File/function: `crates/fullnode/src/l2_syncer.rs` -> `process_l2_block`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: what a syncing node sees first
- Exploit idea: unverified data persisted as canonical - reach `process_l2_block` from that entrypoint and force the divergence where the data a node persists and the data it has verified stop being the same set; the adjacent symbols in the same file that carry the value are `L2Syncer`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only verified data becomes canonical
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: persist a pending proof and assert it is not served as final
