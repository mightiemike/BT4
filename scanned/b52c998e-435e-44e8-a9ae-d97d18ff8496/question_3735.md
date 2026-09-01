# Q3735: estimateGas versus execution via `blockhash_set` (provider_functions.rs)

## Question
Can an unprivileged attacker who queries a slot it wrote at `pending`, `latest` and a block hash tag, controlling the storage slots its contract touches, drive `blockhash_set` in `crates/evm/src/provider_functions.rs` so that the gas `eth_estimateGas` reports and the gas the same call consumes on-chain stop being equal, breaking the invariant that estimation is an upper bound for the same state?

## Target
- File/function: `crates/evm/src/provider_functions.rs` -> `blockhash_set`
- Entrypoint: unprivileged party queries a slot it wrote at `pending`, `latest` and a block hash tag
- Attacker controls: the storage slots its contract touches
- Exploit idea: estimateGas versus execution - reach `blockhash_set` from that entrypoint and force the divergence where the gas `eth_estimateGas` reports and the gas the same call consumes on-chain stop being equal; the adjacent symbols in the same file that carry the value are `account_exists`, `account_info`, `account_set`, `get_storage_address`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: estimation is an upper bound for the same state
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: estimate then execute an L1-fee-heavy call and compare
