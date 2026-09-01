# Q1058: oversized blob accepted via `eth_get_raw_transaction_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the `calc_tx_id` preimage, drive `eth_get_raw_transaction_by_hash` in `crates/sequencer/src/rpc.rs` so that the blob length the sequencer admits and the length the Bridge call can encode stop being compatible, breaking the invariant that an admitted deposit is always encodable into a block?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `eth_get_raw_transaction_by_hash`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the `calc_tx_id` preimage
- Exploit idea: oversized blob accepted - reach `eth_get_raw_transaction_by_hash` from that entrypoint and force the divergence where the blob length the sequencer admits and the length the Bridge call can encode stop being compatible; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an admitted deposit is always encodable into a block
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: submit a maximal blob and assert block production still completes
