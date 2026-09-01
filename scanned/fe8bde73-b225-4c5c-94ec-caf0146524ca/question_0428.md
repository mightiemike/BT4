# Q0428: deposit replay across blocks via `txpool_remove_txs_by_sender` (rpc.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the `calc_tx_id` preimage, drive `txpool_remove_txs_by_sender` in `crates/sequencer/src/rpc.rs` so that the number of times a given deposit blob is executed as a `SYSTEM_SIGNER` transaction and the number of times it was funded on Bitcoin stop being equal, breaking the invariant that each deposit is minted exactly once?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `txpool_remove_txs_by_sender`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: deposit replay across blocks - reach `txpool_remove_txs_by_sender` from that entrypoint and force the divergence where the number of times a given deposit blob is executed as a `SYSTEM_SIGNER` transaction and the number of times it was funded on Bitcoin stop being equal; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each deposit is minted exactly once
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: resubmit an already-included blob and assert the second inclusion reverts
