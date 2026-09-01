# Q1328: oversized blob accepted via `create_rpc_context` (rpc.rs)

## Question
Can an unprivileged attacker who submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies, controlling the `calc_tx_id` preimage, drive `create_rpc_context` in `crates/sequencer/src/rpc.rs` so that the blob length the sequencer admits and the length the Bridge call can encode stop being compatible, breaking the invariant that an admitted deposit is always encodable into a block?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `create_rpc_context`
- Entrypoint: unprivileged party submits two deposit blobs that derive the same `calc_tx_id` but carry different bodies
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: oversized blob accepted - reach `create_rpc_context` from that entrypoint and force the divergence where the blob length the sequencer admits and the length the Bridge call can encode stop being compatible; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `register_rpc_methods`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an admitted deposit is always encodable into a block
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: submit a maximal blob and assert block production still completes
