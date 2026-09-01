# Q5305: filter surviving a prune boundary via `generate_account_proof` (mod.rs)

## Question
Can an unprivileged attacker who emits logs from an attacker-deployed contract and then queries them back, controlling poll timing across blocks, drive `generate_account_proof` in `crates/evm/src/rpc_helpers/mod.rs` so that the range a filter still answers for and the range the node can still prove stop being the same, breaking the invariant that answers are bounded by provable history?

## Target
- File/function: `crates/evm/src/rpc_helpers/mod.rs` -> `generate_account_proof`
- Entrypoint: unprivileged party emits logs from an attacker-deployed contract and then queries them back
- Attacker controls: poll timing across blocks
- Exploit idea: filter surviving a prune boundary - reach `generate_account_proof` from that entrypoint and force the divergence where the range a filter still answers for and the range the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `apply_state_overrides`, `apply_account_override`, `apply_block_overrides`, `generate_eth_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: answers are bounded by provable history
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: poll a filter across the prune horizon and assert an explicit error
