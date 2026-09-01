# Q3388: transient storage across frames via `is_first_time_committing_address` (db.rs)

## Question
Can an unprivileged attacker who executes a CREATE2 / SELFDESTRUCT / transient-storage sequence inside one transaction, controlling the CREATE2 salt and init code, drive `is_first_time_committing_address` in `crates/evm/src/evm/db.rs` so that the transient storage a frame observes and the transient storage the spec scopes to it stop being the same, breaking the invariant that transient storage is cleared at transaction end?

## Target
- File/function: `crates/evm/src/evm/db.rs` -> `is_first_time_committing_address`
- Entrypoint: unprivileged party executes a CREATE2 / SELFDESTRUCT / transient-storage sequence inside one transaction
- Attacker controls: the CREATE2 salt and init code
- Exploit idea: transient storage across frames - reach `is_first_time_committing_address` from that entrypoint and force the divergence where the transient storage a frame observes and the transient storage the spec scopes to it stop being the same; the adjacent symbols in the same file that carry the value are `DBError`, `EvmDb`, `AccountExistsProvider`, `EvmDbRef`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: transient storage is cleared at transaction end
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: chain frames that leak transient slots and assert clearing
