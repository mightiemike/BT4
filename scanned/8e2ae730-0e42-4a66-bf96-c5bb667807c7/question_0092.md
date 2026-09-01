# Q0092: pending transaction served as canonical via `create_rpc_context` (rpc.rs)

## Question
Can an unprivileged attacker who queries `eth_getRawTransactionByHash` for a transaction it just submitted, controlling the timing of the query relative to sealing, drive `create_rpc_context` in `crates/sequencer/src/rpc.rs` so that the transaction body returned by `eth_getTransactionByHash` from the mempool and the body eventually mined stop being identical, breaking the invariant that an RPC answer about a hash is never contradicted by the mined chain?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `create_rpc_context`
- Entrypoint: unprivileged party queries `eth_getRawTransactionByHash` for a transaction it just submitted
- Attacker controls: the timing of the query relative to sealing
- Exploit idea: pending transaction served as canonical - reach `create_rpc_context` from that entrypoint and force the divergence where the transaction body returned by `eth_getTransactionByHash` from the mempool and the body eventually mined stop being identical; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `register_rpc_methods`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an RPC answer about a hash is never contradicted by the mined chain
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: return a mempool hit, mine a different body under the same hash path, and diff
