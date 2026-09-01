# Q0110: fork lookup at exact boundary via `fork_from_block_number` (forks.rs)

## Question
Can an unprivileged attacker who sends a transaction at the exact block where a fork rule changes, controlling the exact activation block, drive `fork_from_block_number` in `crates/primitives/src/forks.rs` so that the fork `fork_from_block_number` returns at height N and the fork the rest of the stack applies stop being the same, breaking the invariant that one height maps to exactly one fork everywhere?

## Target
- File/function: `crates/primitives/src/forks.rs` -> `fork_from_block_number`
- Entrypoint: unprivileged party sends a transaction at the exact block where a fork rule changes
- Attacker controls: the exact activation block
- Exploit idea: fork lookup at exact boundary - reach `fork_from_block_number` from that entrypoint and force the divergence where the fork `fork_from_block_number` returns at height N and the fork the rest of the stack applies stop being the same; the adjacent symbols in the same file that carry the value are `use_network_forks`, `get_forks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: one height maps to exactly one fork everywhere
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: query every consumer at the boundary height and compare
