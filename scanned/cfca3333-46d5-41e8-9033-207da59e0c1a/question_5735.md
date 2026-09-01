# Q5735: pending tag semantics via `apply_block_overrides` (mod.rs)

## Question
Can an unprivileged attacker who calls `eth_estimateGas` on a contract that reads block and L1 state, controlling call overrides and state overrides, drive `apply_block_overrides` in `crates/evm/src/rpc_helpers/mod.rs` so that the state the `pending` tag exposes and the state the next block actually starts from stop being the same, breaking the invariant that the pending view never contradicts the block that follows?

## Target
- File/function: `crates/evm/src/rpc_helpers/mod.rs` -> `apply_block_overrides`
- Entrypoint: unprivileged party calls `eth_estimateGas` on a contract that reads block and L1 state
- Attacker controls: call overrides and state overrides
- Exploit idea: pending tag semantics - reach `apply_block_overrides` from that entrypoint and force the divergence where the state the `pending` tag exposes and the state the next block actually starts from stop being the same; the adjacent symbols in the same file that carry the value are `apply_state_overrides`, `apply_account_override`, `generate_eth_proof`, `generate_account_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the pending view never contradicts the block that follows
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: read pending, seal the block, and diff
