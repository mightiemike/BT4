# Q4938: prev-hash chaining via `read_json_file` (genesis_config.rs)

## Question
Can an unprivileged attacker who sends a transaction at an exact fork activation height, controlling the fork-activation height it targets, drive `read_json_file` in `crates/citrea-stf/src/genesis_config.rs` so that the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal, breaking the invariant that L2 blocks form a hash chain with no forks?

## Target
- File/function: `crates/citrea-stf/src/genesis_config.rs` -> `read_json_file`
- Entrypoint: unprivileged party sends a transaction at an exact fork activation height
- Attacker controls: the fork-activation height it targets
- Exploit idea: prev-hash chaining - reach `read_json_file` from that entrypoint and force the divergence where the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal; the adjacent symbols in the same file that carry the value are `GenesisPaths`, `from_dir`, `get_genesis_config`, `validate_config`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L2 blocks form a hash chain with no forks
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert a block with a mismatched parent and assert rejection
