# Q1988: raw transaction re-encoding via `resume_commitments` (rpc.rs)

## Question
Can an unprivileged attacker who queries `eth_getRawTransactionByHash` for a transaction it just submitted, controlling the timing of the query relative to sealing, drive `resume_commitments` in `crates/sequencer/src/rpc.rs` so that the raw bytes `eth_getRawTransactionByHash` returns and the bytes whose hash was requested stop being the same encoding, breaking the invariant that raw re-encoding round-trips to the same hash?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `resume_commitments`
- Entrypoint: unprivileged party queries `eth_getRawTransactionByHash` for a transaction it just submitted
- Attacker controls: the timing of the query relative to sealing
- Exploit idea: raw transaction re-encoding - reach `resume_commitments` from that entrypoint and force the divergence where the raw bytes `eth_getRawTransactionByHash` returns and the bytes whose hash was requested stop being the same encoding; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: raw re-encoding round-trips to the same hash
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: round-trip every supported transaction type through the RPC encoder
