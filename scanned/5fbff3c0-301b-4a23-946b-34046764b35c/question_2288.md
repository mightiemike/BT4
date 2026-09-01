# Q2288: raw transaction re-encoding via `txpool_remove_txs_by_sender` (rpc.rs)

## Question
Can an unprivileged attacker who queries `eth_getTransactionByHash` for a transaction still only in the mempool, controlling the timing of the query relative to sealing, drive `txpool_remove_txs_by_sender` in `crates/sequencer/src/rpc.rs` so that the raw bytes `eth_getRawTransactionByHash` returns and the bytes whose hash was requested stop being the same encoding, breaking the invariant that raw re-encoding round-trips to the same hash?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `txpool_remove_txs_by_sender`
- Entrypoint: unprivileged party queries `eth_getTransactionByHash` for a transaction still only in the mempool
- Attacker controls: the timing of the query relative to sealing
- Exploit idea: raw transaction re-encoding - reach `txpool_remove_txs_by_sender` from that entrypoint and force the divergence where the raw bytes `eth_getRawTransactionByHash` returns and the bytes whose hash was requested stop being the same encoding; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: raw re-encoding round-trips to the same hash
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: round-trip every supported transaction type through the RPC encoder
