# Q3083: filter surviving a prune boundary via `create_rpc_module` (lib.rs)

## Question
Can an unprivileged attacker who emits logs from an attacker-deployed contract and then queries them back, controlling poll timing across blocks, drive `create_rpc_module` in `crates/ethereum-rpc/src/lib.rs` so that the range a filter still answers for and the range the node can still prove stop being the same, breaking the invariant that answers are bounded by provable history?

## Target
- File/function: `crates/ethereum-rpc/src/lib.rs` -> `create_rpc_module`
- Entrypoint: unprivileged party emits logs from an attacker-deployed contract and then queries them back
- Attacker controls: poll timing across blocks
- Exploit idea: filter surviving a prune boundary - reach `create_rpc_module` from that entrypoint and force the divergence where the range a filter still answers for and the range the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `SyncValues`, `LayerStatus`, `SyncStatus`, `EthereumRpc`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: answers are bounded by provable history
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: poll a filter across the prune horizon and assert an explicit error
