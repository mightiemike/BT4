# Q1318: removal by txid over-matching via `create_rpc_context` (rpc.rs)

## Question
Can an unprivileged attacker who calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob, controlling the `calc_tx_id` preimage, drive `create_rpc_context` in `crates/sequencer/src/rpc.rs` so that the deposits `remove_deposits` erases and the deposits actually included in the block stop being the same set, breaking the invariant that only included deposits leave the mempool?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `create_rpc_context`
- Entrypoint: unprivileged party calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: removal by txid over-matching - reach `create_rpc_context` from that entrypoint and force the divergence where the deposits `remove_deposits` erases and the deposits actually included in the block stop being the same set; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `register_rpc_methods`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: only included deposits leave the mempool
- Expected Immunefi impact: Critical - permanent freezing of funds (recovery requires a hard fork)
- Fast validation: include one deposit whose txid collides with a pending one and assert the pending one survives
