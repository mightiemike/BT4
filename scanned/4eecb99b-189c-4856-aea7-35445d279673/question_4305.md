# Q4305: filter surviving a prune boundary via `to_eth_rpc_error` (lib.rs)

## Question
Can an unprivileged attacker who installs a filter and polls it across a block boundary, controlling the log-emitting contract's bytecode, drive `to_eth_rpc_error` in `crates/ethereum-rpc/src/lib.rs` so that the range a filter still answers for and the range the node can still prove stop being the same, breaking the invariant that answers are bounded by provable history?

## Target
- File/function: `crates/ethereum-rpc/src/lib.rs` -> `to_eth_rpc_error`
- Entrypoint: unprivileged party installs a filter and polls it across a block boundary
- Attacker controls: the log-emitting contract's bytecode
- Exploit idea: filter surviving a prune boundary - reach `to_eth_rpc_error` from that entrypoint and force the divergence where the range a filter still answers for and the range the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `SyncValues`, `LayerStatus`, `SyncStatus`, `EthereumRpc`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: answers are bounded by provable history
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: poll a filter across the prune horizon and assert an explicit error
