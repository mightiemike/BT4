# Q0474: fork manager migration via `mod` (mod.rs)

## Question
Can an unprivileged attacker who sends transactions at the exact block where a fork or migration activates, controlling the transaction mix at the boundary, drive `mod` in `crates/sovereign-sdk/rollup-interface/src/fork/mod.rs` so that the migration a node applies at a height and the migration the circuit assumes stop being the same, breaking the invariant that migrations are height-deterministic?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/fork/mod.rs` -> `mod`
- Entrypoint: unprivileged party sends transactions at the exact block where a fork or migration activates
- Attacker controls: the transaction mix at the boundary
- Exploit idea: fork manager migration - reach `mod` from that entrypoint and force the divergence where the migration a node applies at a height and the migration the circuit assumes stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: migrations are height-deterministic
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: cross the migration height both ways and diff
