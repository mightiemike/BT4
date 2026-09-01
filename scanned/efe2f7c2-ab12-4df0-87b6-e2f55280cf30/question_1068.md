# Q1068: deposit body decoded twice differently via `txpool_content` (rpc.rs)

## Question
Can an unprivileged attacker who calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob, controlling submission timing relative to block sealing, drive `txpool_content` in `crates/sequencer/src/rpc.rs` so that the deposit body the simulation decodes and the body the block execution decodes stop being the same structure, breaking the invariant that one blob has one decoding?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `txpool_content`
- Entrypoint: unprivileged party calls the unauthenticated `citrea_sendRawDepositTransaction` with a hand-built deposit blob
- Attacker controls: submission timing relative to block sealing
- Exploit idea: deposit body decoded twice differently - reach `txpool_content` from that entrypoint and force the divergence where the deposit body the simulation decodes and the body the block execution decodes stop being the same structure; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: one blob has one decoding
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: craft a body with two valid decodings and assert one is chosen
