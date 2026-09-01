# Q3090: log filter range inversion via `create_rpc_module` (lib.rs)

## Question
Can an unprivileged attacker who calls `eth_getLogs` with a filter range and topic set of their choosing, controlling poll timing across blocks, drive `create_rpc_module` in `crates/ethereum-rpc/src/lib.rs` so that the log set the filter returns and the log set the canonical chain contains for that range stop being the same set, breaking the invariant that an RPC log answer is a subset of the proved chain's logs?

## Target
- File/function: `crates/ethereum-rpc/src/lib.rs` -> `create_rpc_module`
- Entrypoint: unprivileged party calls `eth_getLogs` with a filter range and topic set of their choosing
- Attacker controls: poll timing across blocks
- Exploit idea: log filter range inversion - reach `create_rpc_module` from that entrypoint and force the divergence where the log set the filter returns and the log set the canonical chain contains for that range stop being the same set; the adjacent symbols in the same file that carry the value are `SyncValues`, `LayerStatus`, `SyncStatus`, `EthereumRpc`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an RPC log answer is a subset of the proved chain's logs
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: compare filter output against a direct chain walk for adversarial ranges
