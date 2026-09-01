# Q3293: call override crossing into real state via `apply_account_override` (mod.rs)

## Question
Can an unprivileged attacker who calls `eth_call` against an attacker-deployed contract at a historical block tag, controlling the block tag (`latest`/`pending`/hash), drive `apply_account_override` in `crates/evm/src/rpc_helpers/mod.rs` so that the state an overridden `eth_call` mutates and the ephemeral overlay it is supposed to mutate stop being the same working set, breaking the invariant that no RPC call mutates persisted state?

## Target
- File/function: `crates/evm/src/rpc_helpers/mod.rs` -> `apply_account_override`
- Entrypoint: unprivileged party calls `eth_call` against an attacker-deployed contract at a historical block tag
- Attacker controls: the block tag (`latest`/`pending`/hash)
- Exploit idea: call override crossing into real state - reach `apply_account_override` from that entrypoint and force the divergence where the state an overridden `eth_call` mutates and the ephemeral overlay it is supposed to mutate stop being the same working set; the adjacent symbols in the same file that carry the value are `apply_state_overrides`, `apply_block_overrides`, `generate_eth_proof`, `generate_account_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no RPC call mutates persisted state
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: run an override-heavy call and assert the state root is unchanged afterwards
