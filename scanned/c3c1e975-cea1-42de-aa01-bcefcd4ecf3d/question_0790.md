# Q0790: timestamp rule monotonicity via `hook_handler` (hooks.rs)

## Question
Can an unprivileged attacker who submits transactions timed at the L1-block and L2-block-count rule boundaries, controlling submission timing across L1/L2 block boundaries, drive `hook_handler` in `crates/l2-block-rule-enforcer/src/hooks.rs` so that the timestamp the sequencer sealed and the timestamp the rule enforcer accepts on replay stop being the same, breaking the invariant that block timestamps are monotone and replay-stable?

## Target
- File/function: `crates/l2-block-rule-enforcer/src/hooks.rs` -> `hook_handler`
- Entrypoint: unprivileged party submits transactions timed at the L1-block and L2-block-count rule boundaries
- Attacker controls: submission timing across L1/L2 block boundaries
- Exploit idea: timestamp rule monotonicity - reach `hook_handler` from that entrypoint and force the divergence where the timestamp the sequencer sealed and the timestamp the rule enforcer accepts on replay stop being the same; the adjacent symbols in the same file that carry the value are `apply_block_count_rule`, `apply_timestamp_rule`, `end_l2_block_hook`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block timestamps are monotone and replay-stable
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: seal at the boundary and re-apply the block
