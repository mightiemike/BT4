# Q5755: call override crossing into real state via `apply_block_overrides` (mod.rs)

## Question
Can an unprivileged attacker who calls `eth_estimateGas` on a contract that reads block and L1 state, controlling the storage slots its contract touches, drive `apply_block_overrides` in `crates/evm/src/rpc_helpers/mod.rs` so that the state an overridden `eth_call` mutates and the ephemeral overlay it is supposed to mutate stop being the same working set, breaking the invariant that no RPC call mutates persisted state?

## Target
- File/function: `crates/evm/src/rpc_helpers/mod.rs` -> `apply_block_overrides`
- Entrypoint: unprivileged party calls `eth_estimateGas` on a contract that reads block and L1 state
- Attacker controls: the storage slots its contract touches
- Exploit idea: call override crossing into real state - reach `apply_block_overrides` from that entrypoint and force the divergence where the state an overridden `eth_call` mutates and the ephemeral overlay it is supposed to mutate stop being the same working set; the adjacent symbols in the same file that carry the value are `apply_state_overrides`, `apply_account_override`, `generate_eth_proof`, `generate_account_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: no RPC call mutates persisted state
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: run an override-heavy call and assert the state root is unchanged afterwards
