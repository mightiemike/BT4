# Q0009: log filter range inversion via `max_fee_per_gas` (ethereum.rs)

## Question
Can an unprivileged attacker who calls `eth_getLogs` with a filter range and topic set of their choosing, controlling poll timing across blocks, drive `max_fee_per_gas` in `crates/ethereum-rpc/src/ethereum.rs` so that the log set the filter returns and the log set the canonical chain contains for that range stop being the same set, breaking the invariant that an RPC log answer is a subset of the proved chain's logs?

## Target
- File/function: `crates/ethereum-rpc/src/ethereum.rs` -> `max_fee_per_gas`
- Entrypoint: unprivileged party calls `eth_getLogs` with a filter range and topic set of their choosing
- Attacker controls: poll timing across blocks
- Exploit idea: log filter range inversion - reach `max_fee_per_gas` from that entrypoint and force the divergence where the log set the filter returns and the log set the canonical chain contains for that range stop being the same set; the adjacent symbols in the same file that carry the value are `EthRpcConfig`, `Ethereum`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an RPC log answer is a subset of the proved chain's logs
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: compare filter output against a direct chain walk for adversarial ranges
