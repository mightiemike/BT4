# Q0229: fork boundary applied at different heights via `validate_config` (genesis_config.rs)

## Question
Can an unprivileged attacker who sends a transaction at an exact fork activation height, controlling which JMT keys are read and written, drive `validate_config` in `crates/citrea-stf/src/genesis_config.rs` so that the fork the native node applies at height N and the fork the circuit applies stop being the same, breaking the invariant that fork activation is a pure function of height?

## Target
- File/function: `crates/citrea-stf/src/genesis_config.rs` -> `validate_config`
- Entrypoint: unprivileged party sends a transaction at an exact fork activation height
- Attacker controls: which JMT keys are read and written
- Exploit idea: fork boundary applied at different heights - reach `validate_config` from that entrypoint and force the divergence where the fork the native node applies at height N and the fork the circuit applies stop being the same; the adjacent symbols in the same file that carry the value are `GenesisPaths`, `from_dir`, `get_genesis_config`, `create_genesis_config`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fork activation is a pure function of height
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: execute a boundary block both ways and diff
