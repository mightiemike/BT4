# Q1823: receipt/log index skew via `eth_send_raw_transaction` (lib.rs)

## Question
Can an unprivileged attacker who installs a filter and polls it across a block boundary, controlling the log-emitting contract's bytecode, drive `eth_send_raw_transaction` in `crates/ethereum-rpc/src/lib.rs` so that the log index in a returned receipt and the index the block's log ordering assigns stop being equal, breaking the invariant that log indices are stable for a canonical block?

## Target
- File/function: `crates/ethereum-rpc/src/lib.rs` -> `eth_send_raw_transaction`
- Entrypoint: unprivileged party installs a filter and polls it across a block boundary
- Attacker controls: the log-emitting contract's bytecode
- Exploit idea: receipt/log index skew - reach `eth_send_raw_transaction` from that entrypoint and force the divergence where the log index in a returned receipt and the index the block's log ordering assigns stop being equal; the adjacent symbols in the same file that carry the value are `SyncValues`, `LayerStatus`, `SyncStatus`, `EthereumRpc`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: log indices are stable for a canonical block
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: compare receipts against a full-block re-derivation
