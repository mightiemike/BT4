# Q2308: queue ordering versus fee market via `txpool_remove_txs_by_sender` (rpc.rs)

## Question
Can an unprivileged attacker who submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height, controlling submission timing relative to block sealing, drive `txpool_remove_txs_by_sender` in `crates/sequencer/src/rpc.rs` so that the deposit ordering the sequencer commits to and the ordering the Bitcoin side established stop being the same order, breaking the invariant that deposit credit order does not change which deposits succeed?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `txpool_remove_txs_by_sender`
- Entrypoint: unprivileged party submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height
- Attacker controls: submission timing relative to block sealing
- Exploit idea: queue ordering versus fee market - reach `txpool_remove_txs_by_sender` from that entrypoint and force the divergence where the deposit ordering the sequencer commits to and the ordering the Bitcoin side established stop being the same order; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: deposit credit order does not change which deposits succeed
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: reorder the FIFO queue under attacker load and assert all deposits still mint
