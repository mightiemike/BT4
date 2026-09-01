# Q2901: filter surviving a prune boundary via `new_block_filter` (lib.rs)

## Question
Can an unprivileged attacker who calls `eth_getLogs` with a filter range and topic set of their choosing, controlling poll timing across blocks, drive `new_block_filter` in `crates/ethereum-rpc/src/lib.rs` so that the range a filter still answers for and the range the node can still prove stop being the same, breaking the invariant that answers are bounded by provable history?

## Target
- File/function: `crates/ethereum-rpc/src/lib.rs` -> `new_block_filter`
- Entrypoint: unprivileged party calls `eth_getLogs` with a filter range and topic set of their choosing
- Attacker controls: poll timing across blocks
- Exploit idea: filter surviving a prune boundary - reach `new_block_filter` from that entrypoint and force the divergence where the range a filter still answers for and the range the node can still prove stop being the same; the adjacent symbols in the same file that carry the value are `SyncValues`, `LayerStatus`, `SyncStatus`, `EthereumRpc`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: answers are bounded by provable history
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: poll a filter across the prune horizon and assert an explicit error
