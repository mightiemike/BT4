# Q4555: filter surviving a prune boundary via `apply_block_overrides` (mod.rs)

## Question
Can an unprivileged attacker who installs a filter and polls it across a block boundary, controlling the log-emitting contract's bytecode, drive `apply_block_overrides` in `crates/evm/src/rpc_helpers/mod.rs` so that the range a filter still answers for and the range the node can still prove stop being the same, breaking the invariant that answers are bounded by provable history?

## Target
- File/function: `crates/evm/src/rpc_helpers/mod.rs` -> `apply_block_overrides`
- Entrypoint: unprivileged party installs a filter and polls it across a block boundary
- Attacker controls: the log-emitting contract's bytecode
- Exploit idea: filter surviving a prune boundary - reach `apply_block_overrides` from that entrypoint and force the divergence where the range a filter still answers for and the range the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `apply_state_overrides`, `apply_account_override`, `generate_eth_proof`, `generate_account_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: answers are bounded by provable history
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: poll a filter across the prune horizon and assert an explicit error
