# Q2048: pending_deposits set desync via `send_raw_deposit_transaction` (rpc.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling the `calc_tx_id` preimage, drive `send_raw_deposit_transaction` in `crates/sequencer/src/rpc.rs` so that the set of pending deposit txids and the deposits actually queued stop being the same set, breaking the invariant that the dedup set mirrors the queue?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `send_raw_deposit_transaction`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: pending_deposits set desync - reach `send_raw_deposit_transaction` from that entrypoint and force the divergence where the set of pending deposit txids and the deposits actually queued stop being the same set; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the dedup set mirrors the queue
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: drive add/remove races and assert set equality
