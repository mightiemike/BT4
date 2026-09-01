# Q1478: oversized blob accepted via `txpool_content` (rpc.rs)

## Question
Can an unprivileged attacker who calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob, controlling submission timing relative to block sealing, drive `txpool_content` in `crates/sequencer/src/rpc.rs` so that the blob length the sequencer admits and the length the Bridge call can encode stop being compatible, breaking the invariant that an admitted deposit is always encodable into a block?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `txpool_content`
- Entrypoint: unprivileged party calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob
- Attacker controls: submission timing relative to block sealing
- Exploit idea: oversized blob accepted - reach `txpool_content` from that entrypoint and force the divergence where the blob length the sequencer admits and the length the Bridge call can encode stop being compatible; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an admitted deposit is always encodable into a block
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: submit a maximal blob and assert block production still completes
