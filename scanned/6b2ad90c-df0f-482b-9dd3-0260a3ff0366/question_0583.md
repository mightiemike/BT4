# Q0583: query executed against a pruned root via `last_sealed_header` (provider_functions.rs)

## Question
Can an unprivileged attacker who queries a slot it wrote at `pending`, `latest` and a block hash tag, controlling call overrides and state overrides, drive `last_sealed_header` in `crates/evm/src/provider_functions.rs` so that the root a query executes against and a root the node can still prove stop being the same, breaking the invariant that queries never answer from unprovable state?

## Target
- File/function: `crates/evm/src/provider_functions.rs` -> `last_sealed_header`
- Entrypoint: unprivileged party queries a slot it wrote at `pending`, `latest` and a block hash tag
- Attacker controls: call overrides and state overrides
- Exploit idea: query executed against a pruned root - reach `last_sealed_header` from that entrypoint and force the divergence where the root a query executes against and a root the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `account_exists`, `account_info`, `account_set`, `get_storage_address`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: queries never answer from unprovable state
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: query below the prune horizon and assert an explicit error
