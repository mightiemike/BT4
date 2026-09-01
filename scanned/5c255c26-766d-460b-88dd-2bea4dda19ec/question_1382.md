# Q1382: unverified data persisted as canonical via `sync_l2` (l2.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that a syncing full node must process before it has the matching proof, controlling conflicting L1 data across a reorg, drive `sync_l2` in `crates/common/src/l2.rs` so that the data a node persists and the data it has verified stop being the same set, breaking the invariant that only verified data becomes canonical?

## Target
- File/function: `crates/common/src/l2.rs` -> `sync_l2`
- Entrypoint: unprivileged party inscribes L1 data that a syncing full node must process before it has the matching proof
- Attacker controls: conflicting L1 data across a reorg
- Exploit idea: unverified data persisted as canonical - reach `sync_l2` from that entrypoint and force the divergence where the data a node persists and the data it has verified stop being the same set; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `execute_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only verified data becomes canonical
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: persist a pending proof and assert it is not served as final
