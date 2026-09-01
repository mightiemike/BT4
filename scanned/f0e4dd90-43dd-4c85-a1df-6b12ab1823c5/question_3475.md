# Q3475: selfdestruct/recreate accounting via `db_init` (db_init.rs)

## Question
Can an unprivileged attacker who deploys at a salt it previously destroyed, controlling the CREATE2 salt and init code, drive `db_init` in `crates/evm/src/evm/db_init.rs` so that the balance destroyed and the balance recreated at the same address stop summing to the pre-state, breaking the invariant that supply is conserved across account lifecycle operations?

## Target
- File/function: `crates/evm/src/evm/db_init.rs` -> `db_init`
- Entrypoint: unprivileged party deploys at a salt it previously destroyed
- Attacker controls: the CREATE2 salt and init code
- Exploit idea: selfdestruct/recreate accounting - reach `db_init` from that entrypoint and force the divergence where the balance destroyed and the balance recreated at the same address stop summing to the pre-state; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: supply is conserved across account lifecycle operations
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: run destroy-and-recreate in one block and assert total supply
