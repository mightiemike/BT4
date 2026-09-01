# Q1207: selfdestruct/recreate accounting via `is_first_time_committing_address` (db.rs)

## Question
Can an unprivileged attacker who executes a CREATE2 / SELFDESTRUCT / transient-storage sequence inside one transaction, controlling the CREATE2 salt and init code, drive `is_first_time_committing_address` in `crates/evm/src/evm/db.rs` so that the balance destroyed and the balance recreated at the same address stop summing to the pre-state, breaking the invariant that supply is conserved across account lifecycle operations?

## Target
- File/function: `crates/evm/src/evm/db.rs` -> `is_first_time_committing_address`
- Entrypoint: unprivileged party executes a CREATE2 / SELFDESTRUCT / transient-storage sequence inside one transaction
- Attacker controls: the CREATE2 salt and init code
- Exploit idea: selfdestruct/recreate accounting - reach `is_first_time_committing_address` from that entrypoint and force the divergence where the balance destroyed and the balance recreated at the same address stop summing to the pre-state; the adjacent symbols in the same file that carry the value are `DBError`, `EvmDb`, `AccountExistsProvider`, `EvmDbRef`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: supply is conserved across account lifecycle operations
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: run destroy-and-recreate in one block and assert total supply
