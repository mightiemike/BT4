# Q3725: log filter range inversion via `eth_max_priority_fee_per_gas` (lib.rs)

## Question
Can an unprivileged attacker who installs a filter and polls it across a block boundary, controlling poll timing across blocks, drive `eth_max_priority_fee_per_gas` in `crates/ethereum-rpc/src/lib.rs` so that the log set the filter returns and the log set the canonical chain contains for that range stop being the same set, breaking the invariant that an RPC log answer is a subset of the proved chain's logs?

## Target
- File/function: `crates/ethereum-rpc/src/lib.rs` -> `eth_max_priority_fee_per_gas`
- Entrypoint: unprivileged party installs a filter and polls it across a block boundary
- Attacker controls: poll timing across blocks
- Exploit idea: log filter range inversion - reach `eth_max_priority_fee_per_gas` from that entrypoint and force the divergence where the log set the filter returns and the log set the canonical chain contains for that range stop being the same set; the adjacent symbols in the same file that carry the value are `SyncValues`, `LayerStatus`, `SyncStatus`, `EthereumRpc`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an RPC log answer is a subset of the proved chain's logs
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: compare filter output against a direct chain walk for adversarial ranges
