# Q2188: deposit body decoded twice differently via `txpool_remove_txs_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the number of competing blobs queued, drive `txpool_remove_txs_by_hash` in `crates/sequencer/src/rpc.rs` so that the deposit body the simulation decodes and the body the block execution decodes stop being the same structure, breaking the invariant that one blob has one decoding?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `txpool_remove_txs_by_hash`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the number of competing blobs queued
- Exploit idea: deposit body decoded twice differently - reach `txpool_remove_txs_by_hash` from that entrypoint and force the divergence where the deposit body the simulation decodes and the body the block execution decodes stop being the same structure; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: one blob has one decoding
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: craft a body with two valid decodings and assert one is chosen
