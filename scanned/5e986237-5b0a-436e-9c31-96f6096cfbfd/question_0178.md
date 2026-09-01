# Q0178: pending_deposits set desync via `register_rpc_methods` (rpc.rs)

## Question
Can an unprivileged attacker who submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height, controlling the `calc_tx_id` preimage, drive `register_rpc_methods` in `crates/sequencer/src/rpc.rs` so that the set of pending deposit txids and the deposits actually queued stop being the same set, breaking the invariant that the dedup set mirrors the queue?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `register_rpc_methods`
- Entrypoint: unprivileged party submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: pending_deposits set desync - reach `register_rpc_methods` from that entrypoint and force the divergence where the set of pending deposit txids and the deposits actually queued stop being the same set; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the dedup set mirrors the queue
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: drive add/remove races and assert set equality
