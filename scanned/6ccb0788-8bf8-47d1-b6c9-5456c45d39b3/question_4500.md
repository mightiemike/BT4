# Q4500: historical state served from wrong root via `account_set` (provider_functions.rs)

## Question
Can an unprivileged attacker who calls `eth_call` against an attacker-deployed contract at a historical block tag, controlling call overrides and state overrides, drive `account_set` in `crates/evm/src/provider_functions.rs` so that the state root the query executes against and the root of the block tag requested stop being the same root, breaking the invariant that every query answer is anchored to the requested block's state root?

## Target
- File/function: `crates/evm/src/provider_functions.rs` -> `account_set`
- Entrypoint: unprivileged party calls `eth_call` against an attacker-deployed contract at a historical block tag
- Attacker controls: call overrides and state overrides
- Exploit idea: historical state served from wrong root - reach `account_set` from that entrypoint and force the divergence where the state root the query executes against and the root of the block tag requested stop being the same root; the adjacent symbols in the same file that carry the value are `account_exists`, `account_info`, `get_storage_address`, `storage_get`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every query answer is anchored to the requested block's state root
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: query an attacker-touched slot at a historical tag and diff against archival replay
