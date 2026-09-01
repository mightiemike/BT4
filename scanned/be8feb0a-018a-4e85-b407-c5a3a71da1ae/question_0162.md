# Q0162: unverified data persisted as canonical via `commit_l2_block` (l2.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling what a syncing node sees first, drive `commit_l2_block` in `crates/common/src/l2.rs` so that the data a node persists and the data it has verified stop being the same set, breaking the invariant that only verified data becomes canonical?

## Target
- File/function: `crates/common/src/l2.rs` -> `commit_l2_block`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: what a syncing node sees first
- Exploit idea: unverified data persisted as canonical - reach `commit_l2_block` from that entrypoint and force the divergence where the data a node persists and the data it has verified stop being the same set; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `execute_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only verified data becomes canonical
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: persist a pending proof and assert it is not served as final
