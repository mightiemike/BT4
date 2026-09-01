# Q5759: pending tag semantics via `generate_eth_proof` (mod.rs)

## Question
Can an unprivileged attacker who calls `eth_call` against an attacker-deployed contract at a historical block tag, controlling the block tag (`latest`/`pending`/hash), drive `generate_eth_proof` in `crates/evm/src/rpc_helpers/mod.rs` so that the state the `pending` tag exposes and the state the next block actually starts from stop being the same, breaking the invariant that the pending view never contradicts the block that follows?

## Target
- File/function: `crates/evm/src/rpc_helpers/mod.rs` -> `generate_eth_proof`
- Entrypoint: unprivileged party calls `eth_call` against an attacker-deployed contract at a historical block tag
- Attacker controls: the block tag (`latest`/`pending`/hash)
- Exploit idea: pending tag semantics - reach `generate_eth_proof` from that entrypoint and force the divergence where the state the `pending` tag exposes and the state the next block actually starts from stop being the same; the adjacent symbols in the same file that carry the value are `apply_state_overrides`, `apply_account_override`, `apply_block_overrides`, `generate_account_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the pending view never contradicts the block that follows
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: read pending, seal the block, and diff
