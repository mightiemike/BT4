# Q2098: deposit replay across blocks via `publish_test_block` (rpc.rs)

## Question
Can an unprivileged attacker who submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height, controlling submission timing relative to block sealing, drive `publish_test_block` in `crates/sequencer/src/rpc.rs` so that the number of times a given deposit blob is executed as a `SYSTEM_SIGNER` transaction and the number of times it was funded on Bitcoin stop being equal, breaking the invariant that each deposit is minted exactly once?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `publish_test_block`
- Entrypoint: unprivileged party submits a deposit blob whose `eth_call` simulation succeeds against current state but not at inclusion height
- Attacker controls: submission timing relative to block sealing
- Exploit idea: deposit replay across blocks - reach `publish_test_block` from that entrypoint and force the divergence where the number of times a given deposit blob is executed as a `SYSTEM_SIGNER` transaction and the number of times it was funded on Bitcoin stop being equal; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each deposit is minted exactly once
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: resubmit an already-included blob and assert the second inclusion reverts
