# Q2127: selfdestruct/recreate accounting via `try_from` (conversions.rs)

## Question
Can an unprivileged attacker who deploys at a salt it previously destroyed, controlling the account lifecycle sequence, drive `try_from` in `crates/evm/src/evm/conversions.rs` so that the balance destroyed and the balance recreated at the same address stop summing to the pre-state, breaking the invariant that supply is conserved across account lifecycle operations?

## Target
- File/function: `crates/evm/src/evm/conversions.rs` -> `try_from`
- Entrypoint: unprivileged party deploys at a salt it previously destroyed
- Attacker controls: the account lifecycle sequence
- Exploit idea: selfdestruct/recreate accounting - reach `try_from` from that entrypoint and force the divergence where the balance destroyed and the balance recreated at the same address stop summing to the pre-state; the adjacent symbols in the same file that carry the value are `ConversionError`, `create_tx_env`, `sealed_block_to_block_env`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: supply is conserved across account lifecycle operations
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: run destroy-and-recreate in one block and assert total supply
