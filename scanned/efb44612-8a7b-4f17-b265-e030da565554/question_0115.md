# Q0115: state diff accumulation via `get_genesis_config` (genesis_config.rs)

## Question
Can an unprivileged attacker who sends a transaction at an exact fork activation height, controlling the fork-activation height it targets, drive `get_genesis_config` in `crates/citrea-stf/src/genesis_config.rs` so that the state diff the circuit outputs and the diff the DA blob carries stop being the same diff, breaking the invariant that published diffs equal proved diffs?

## Target
- File/function: `crates/citrea-stf/src/genesis_config.rs` -> `get_genesis_config`
- Entrypoint: unprivileged party sends a transaction at an exact fork activation height
- Attacker controls: the fork-activation height it targets
- Exploit idea: state diff accumulation - reach `get_genesis_config` from that entrypoint and force the divergence where the state diff the circuit outputs and the diff the DA blob carries stop being the same diff; the adjacent symbols in the same file that carry the value are `GenesisPaths`, `from_dir`, `validate_config`, `create_genesis_config`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: published diffs equal proved diffs
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: diff the published blob against the circuit output
