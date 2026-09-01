# Q2042: l2 sync trusting sequencer signature via `get_l2_blocks_range` (l2.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling conflicting L1 data across a reorg, drive `get_l2_blocks_range` in `crates/common/src/l2.rs` so that the block a node accepts from the L2 sync path and the block covered by a signed commitment stop being the same block, breaking the invariant that synced blocks are covered by sequencer authority?

## Target
- File/function: `crates/common/src/l2.rs` -> `get_l2_blocks_range`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: conflicting L1 data across a reorg
- Exploit idea: l2 sync trusting sequencer signature - reach `get_l2_blocks_range` from that entrypoint and force the divergence where the block a node accepts from the L2 sync path and the block covered by a signed commitment stop being the same block; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `execute_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: synced blocks are covered by sequencer authority
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: serve an unsigned block over the sync path and assert rejection
