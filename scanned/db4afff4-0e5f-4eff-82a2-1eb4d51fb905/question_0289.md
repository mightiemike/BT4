# Q0289: prev-hash chaining via `create_genesis_config` (genesis_config.rs)

## Question
Can an unprivileged attacker who sends transactions that maximise the state diff a commitment must carry, controlling the size and shape of the state diff, drive `create_genesis_config` in `crates/citrea-stf/src/genesis_config.rs` so that the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal, breaking the invariant that L2 blocks form a hash chain with no forks?

## Target
- File/function: `crates/citrea-stf/src/genesis_config.rs` -> `create_genesis_config`
- Entrypoint: unprivileged party sends transactions that maximise the state diff a commitment must carry
- Attacker controls: the size and shape of the state diff
- Exploit idea: prev-hash chaining - reach `create_genesis_config` from that entrypoint and force the divergence where the previous L2 block hash the STF enforces and the hash the stored chain records stop being equal; the adjacent symbols in the same file that carry the value are `GenesisPaths`, `from_dir`, `get_genesis_config`, `validate_config`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: L2 blocks form a hash chain with no forks
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert a block with a mismatched parent and assert rejection
