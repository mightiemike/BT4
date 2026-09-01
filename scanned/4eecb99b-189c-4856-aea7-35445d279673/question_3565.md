# Q3565: create2 address/state collision via `transaction_range` (primitive_types.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key, controlling the storage keys touched, drive `transaction_range` in `crates/evm/src/evm/primitive_types.rs` so that the account state at a CREATE2 address before and after redeployment stop being reconciled, breaking the invariant that redeployment never resurrects stale storage?

## Target
- File/function: `crates/evm/src/evm/primitive_types.rs` -> `transaction_range`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key
- Attacker controls: the storage keys touched
- Exploit idea: create2 address/state collision - reach `transaction_range` from that entrypoint and force the divergence where the account state at a CREATE2 address before and after redeployment stop being reconciled; the adjacent symbols in the same file that carry the value are `RlpEvmTransaction`, `TransactionSignedAndRecovered`, `Block`, `SealedBlock`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: redeployment never resurrects stale storage
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: deploy, destroy and redeploy at the same salt and assert clean storage
