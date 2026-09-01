# Q2018: removal by txid over-matching via `eth_get_transaction_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height, controlling the entire `Bytes` deposit payload, drive `eth_get_transaction_by_hash` in `crates/sequencer/src/rpc.rs` so that the deposits `remove_deposits` erases and the deposits actually included in the block stop being the same set, breaking the invariant that only included deposits leave the mempool?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `eth_get_transaction_by_hash`
- Entrypoint: unprivileged party submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: removal by txid over-matching - reach `eth_get_transaction_by_hash` from that entrypoint and force the divergence where the deposits `remove_deposits` erases and the deposits actually included in the block stop being the same set; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only included deposits leave the mempool
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: include one deposit whose txid collides with a pending one and assert the pending one survives
