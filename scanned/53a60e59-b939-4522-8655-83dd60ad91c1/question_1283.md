# Q1283: receipt/log index skew via `debug_trace_block_by_number` (lib.rs)

## Question
Can an unprivileged attacker who installs a filter and polls it across a block boundary, controlling filter ranges, topics and block tags, drive `debug_trace_block_by_number` in `crates/ethereum-rpc/src/lib.rs` so that the log index in a returned receipt and the index the block's log ordering assigns stop being equal, breaking the invariant that log indices are stable for a canonical block?

## Target
- File/function: `crates/ethereum-rpc/src/lib.rs` -> `debug_trace_block_by_number`
- Entrypoint: unprivileged party installs a filter and polls it across a block boundary
- Attacker controls: filter ranges, topics and block tags
- Exploit idea: receipt/log index skew - reach `debug_trace_block_by_number` from that entrypoint and force the divergence where the log index in a returned receipt and the index the block's log ordering assigns stop being equal; the adjacent symbols in the same file that carry the value are `SyncValues`, `LayerStatus`, `SyncStatus`, `EthereumRpc`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: log indices are stable for a canonical block
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: compare receipts against a full-block re-derivation
