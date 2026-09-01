# Q2372: da handler height bookkeeping via `sync_l2` (l2.rs)

## Question
Can an unprivileged attacker who inscribes conflicting commitments and proofs across an L1 reorg boundary, controlling conflicting L1 data across a reorg, drive `sync_l2` in `crates/common/src/l2.rs` so that the L1 height a node believes processed and the height it actually applied stop being equal, breaking the invariant that processed-height bookkeeping is exact?

## Target
- File/function: `crates/common/src/l2.rs` -> `sync_l2`
- Entrypoint: unprivileged party inscribes conflicting commitments and proofs across an L1 reorg boundary
- Attacker controls: conflicting L1 data across a reorg
- Exploit idea: da handler height bookkeeping - reach `sync_l2` from that entrypoint and force the divergence where the L1 height a node believes processed and the height it actually applied stop being equal; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `execute_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: processed-height bookkeeping is exact
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: crash between apply and record, restart, and diff
