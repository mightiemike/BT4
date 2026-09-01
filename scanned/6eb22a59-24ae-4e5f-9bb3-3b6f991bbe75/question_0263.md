# Q0263: call override crossing into real state via `storage_get` (provider_functions.rs)

## Question
Can an unprivileged attacker who queries a slot it wrote at `pending`, `latest` and a block hash tag, controlling the block tag (`latest`/`pending`/hash), drive `storage_get` in `crates/evm/src/provider_functions.rs` so that the state an overridden `eth_call` mutates and the ephemeral overlay it is supposed to mutate stop being the same working set, breaking the invariant that no RPC call mutates persisted state?

## Target
- File/function: `crates/evm/src/provider_functions.rs` -> `storage_get`
- Entrypoint: unprivileged party queries a slot it wrote at `pending`, `latest` and a block hash tag
- Attacker controls: the block tag (`latest`/`pending`/hash)
- Exploit idea: call override crossing into real state - reach `storage_get` from that entrypoint and force the divergence where the state an overridden `eth_call` mutates and the ephemeral overlay it is supposed to mutate stop being the same working set; the adjacent symbols in the same file that carry the value are `account_exists`, `account_info`, `account_set`, `get_storage_address`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no RPC call mutates persisted state
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: run an override-heavy call and assert the state root is unchanged afterwards
