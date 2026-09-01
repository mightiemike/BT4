# Q0017: timestamp rule monotonicity via `get_authority` (call.rs)

## Question
Can an unprivileged attacker who submits transactions timed at the L1-block and L2-block-count rule boundaries, controlling the number of transactions per block, drive `get_authority` in `crates/l2-block-rule-enforcer/src/call.rs` so that the timestamp the sequencer sealed and the timestamp the rule enforcer accepts on replay stop being the same, breaking the invariant that block timestamps are monotone and replay-stable?

## Target
- File/function: `crates/l2-block-rule-enforcer/src/call.rs` -> `get_authority`
- Entrypoint: unprivileged party submits transactions timed at the L1-block and L2-block-count rule boundaries
- Attacker controls: the number of transactions per block
- Exploit idea: timestamp rule monotonicity - reach `get_authority` from that entrypoint and force the divergence where the timestamp the sequencer sealed and the timestamp the rule enforcer accepts on replay stop being the same; the adjacent symbols in the same file that carry the value are `CallMessage`, `change_authority`, `modify_max_l2_blocks_per_l1`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block timestamps are monotone and replay-stable
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: seal at the boundary and re-apply the block
