# Q0667: selfdestruct/recreate accounting via `override_set_account_storage` (db.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key, controlling the CREATE2 salt and init code, drive `override_set_account_storage` in `crates/evm/src/evm/db.rs` so that the balance destroyed and the balance recreated at the same address stop summing to the pre-state, breaking the invariant that supply is conserved across account lifecycle operations?

## Target
- File/function: `crates/evm/src/evm/db.rs` -> `override_set_account_storage`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key
- Attacker controls: the CREATE2 salt and init code
- Exploit idea: selfdestruct/recreate accounting - reach `override_set_account_storage` from that entrypoint and force the divergence where the balance destroyed and the balance recreated at the same address stop summing to the pre-state; the adjacent symbols in the same file that carry the value are `DBError`, `EvmDb`, `AccountExistsProvider`, `EvmDbRef`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: supply is conserved across account lifecycle operations
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: run destroy-and-recreate in one block and assert total supply
