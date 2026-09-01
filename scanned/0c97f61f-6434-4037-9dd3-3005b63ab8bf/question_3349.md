# Q3349: query executed against a pruned root via `apply_block_overrides` (mod.rs)

## Question
Can an unprivileged attacker who calls `eth_call` against an attacker-deployed contract at a historical block tag, controlling the storage slots its contract touches, drive `apply_block_overrides` in `crates/evm/src/rpc_helpers/mod.rs` so that the root a query executes against and a root the node can still prove stop being the same, breaking the invariant that queries never answer from unprovable state?

## Target
- File/function: `crates/evm/src/rpc_helpers/mod.rs` -> `apply_block_overrides`
- Entrypoint: unprivileged party calls `eth_call` against an attacker-deployed contract at a historical block tag
- Attacker controls: the storage slots its contract touches
- Exploit idea: query executed against a pruned root - reach `apply_block_overrides` from that entrypoint and force the divergence where the root a query executes against and a root the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `apply_state_overrides`, `apply_account_override`, `generate_eth_proof`, `generate_account_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: queries never answer from unprovable state
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: query below the prune horizon and assert an explicit error
