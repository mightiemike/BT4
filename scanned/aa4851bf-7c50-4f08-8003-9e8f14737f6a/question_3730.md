# Q3730: historical state served from wrong root via `storage_set` (provider_functions.rs)

## Question
Can an unprivileged attacker who calls `eth_estimateGas` on a contract that reads block and L1 state, controlling the storage slots its contract touches, drive `storage_set` in `crates/evm/src/provider_functions.rs` so that the state root the query executes against and the root of the block tag requested stop being the same root, breaking the invariant that every query answer is anchored to the requested block's state root?

## Target
- File/function: `crates/evm/src/provider_functions.rs` -> `storage_set`
- Entrypoint: unprivileged party calls `eth_estimateGas` on a contract that reads block and L1 state
- Attacker controls: the storage slots its contract touches
- Exploit idea: historical state served from wrong root - reach `storage_set` from that entrypoint and force the divergence where the state root the query executes against and the root of the block tag requested stop being the same root; the adjacent symbols in the same file that carry the value are `account_exists`, `account_info`, `account_set`, `get_storage_address`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every query answer is anchored to the requested block's state root
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: query an attacker-touched slot at a historical tag and diff against archival replay
