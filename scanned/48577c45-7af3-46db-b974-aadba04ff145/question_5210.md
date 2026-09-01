# Q5210: query executed against a pruned root via `account_info` (provider_functions.rs)

## Question
Can an unprivileged attacker who calls `eth_estimateGas` on a contract that reads block and L1 state, controlling the block tag (`latest`/`pending`/hash), drive `account_info` in `crates/evm/src/provider_functions.rs` so that the root a query executes against and a root the node can still prove stop being the same, breaking the invariant that queries never answer from unprovable state?

## Target
- File/function: `crates/evm/src/provider_functions.rs` -> `account_info`
- Entrypoint: unprivileged party calls `eth_estimateGas` on a contract that reads block and L1 state
- Attacker controls: the block tag (`latest`/`pending`/hash)
- Exploit idea: query executed against a pruned root - reach `account_info` from that entrypoint and force the divergence where the root a query executes against and a root the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `account_exists`, `account_set`, `get_storage_address`, `storage_get`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: queries never answer from unprovable state
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: query below the prune horizon and assert an explicit error
