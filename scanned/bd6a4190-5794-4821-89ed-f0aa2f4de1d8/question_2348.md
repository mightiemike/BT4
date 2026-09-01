# Q2348: pending_deposits set desync via `extract_tx` (rpc.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the ABI encoding of the wrapped Bridge argument, drive `extract_tx` in `crates/sequencer/src/rpc.rs` so that the set of pending deposit txids and the deposits actually queued stop being the same set, breaking the invariant that the dedup set mirrors the queue?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `extract_tx`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the ABI encoding of the wrapped Bridge argument
- Exploit idea: pending_deposits set desync - reach `extract_tx` from that entrypoint and force the divergence where the set of pending deposit txids and the deposits actually queued stop being the same set; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the dedup set mirrors the queue
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: drive add/remove races and assert set equality
