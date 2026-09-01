# Q3778: fork boundary applied at different heights via `get_genesis_config` (genesis_config.rs)

## Question
Can an unprivileged attacker who sends a transaction at an exact fork activation height, controlling the fork-activation height it targets, drive `get_genesis_config` in `crates/citrea-stf/src/genesis_config.rs` so that the fork the native node applies at height N and the fork the circuit applies stop being the same, breaking the invariant that fork activation is a pure function of height?

## Target
- File/function: `crates/citrea-stf/src/genesis_config.rs` -> `get_genesis_config`
- Entrypoint: unprivileged party sends a transaction at an exact fork activation height
- Attacker controls: the fork-activation height it targets
- Exploit idea: fork boundary applied at different heights - reach `get_genesis_config` from that entrypoint and force the divergence where the fork the native node applies at height N and the fork the circuit applies stop being the same; the adjacent symbols in the same file that carry the value are `GenesisPaths`, `from_dir`, `validate_config`, `create_genesis_config`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fork activation is a pure function of height
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: execute a boundary block both ways and diff
