# Q0153: fork manager migration via `active_fork` (manager.rs)

## Question
Can an unprivileged attacker who sends transactions at the exact block where a fork or migration activates, controlling the exact activation block, drive `active_fork` in `crates/sovereign-sdk/rollup-interface/src/fork/manager.rs` so that the migration a node applies at a height and the migration the circuit assumes stop being the same, breaking the invariant that migrations are height-deterministic?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/fork/manager.rs` -> `active_fork`
- Entrypoint: unprivileged party sends transactions at the exact block where a fork or migration activates
- Attacker controls: the exact activation block
- Exploit idea: fork manager migration - reach `active_fork` from that entrypoint and force the divergence where the migration a node applies at a height and the migration the circuit assumes stop being the same; the adjacent symbols in the same file that carry the value are `ForkManager`, `register_handler`, `next_fork`, `register_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: migrations are height-deterministic
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: cross the migration height both ways and diff
