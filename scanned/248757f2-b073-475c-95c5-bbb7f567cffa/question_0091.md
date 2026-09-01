# Q0091: create2 address/state collision via `create_tx_env` (conversions.rs)

## Question
Can an unprivileged attacker who deploys at a salt it previously destroyed, controlling the account lifecycle sequence, drive `create_tx_env` in `crates/evm/src/evm/conversions.rs` so that the account state at a CREATE2 address before and after redeployment stop being reconciled, breaking the invariant that redeployment never resurrects stale storage?

## Target
- File/function: `crates/evm/src/evm/conversions.rs` -> `create_tx_env`
- Entrypoint: unprivileged party deploys at a salt it previously destroyed
- Attacker controls: the account lifecycle sequence
- Exploit idea: create2 address/state collision - reach `create_tx_env` from that entrypoint and force the divergence where the account state at a CREATE2 address before and after redeployment stop being reconciled; the adjacent symbols in the same file that carry the value are `ConversionError`, `try_from`, `sealed_block_to_block_env`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: redeployment never resurrects stale storage
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: deploy, destroy and redeploy at the same salt and assert clean storage
