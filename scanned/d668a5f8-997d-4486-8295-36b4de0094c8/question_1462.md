# Q1462: proof-before-commitment ordering via `get_l2_blocks_range` (l2.rs)

## Question
Can an unprivileged attacker who sends L2 transactions while a full node is mid-sync from genesis, controlling the timing of proof versus commitment arrival, drive `get_l2_blocks_range` in `crates/common/src/l2.rs` so that the state a node adopts when a proof arrives before its commitment and the state when it arrives after stop being the same, breaking the invariant that adoption is order-independent?

## Target
- File/function: `crates/common/src/l2.rs` -> `get_l2_blocks_range`
- Entrypoint: unprivileged party sends L2 transactions while a full node is mid-sync from genesis
- Attacker controls: the timing of proof versus commitment arrival
- Exploit idea: proof-before-commitment ordering - reach `get_l2_blocks_range` from that entrypoint and force the divergence where the state a node adopts when a proof arrives before its commitment and the state when it arrives after stop being the same; the adjacent symbols in the same file that carry the value are `AppliedL2Block`, `SyncError`, `apply_l2_block`, `execute_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: adoption is order-independent
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: deliver in both orders and diff
