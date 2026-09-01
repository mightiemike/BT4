# Q0958: system-signer nonce coupling via `create_rpc_context` (rpc.rs)

## Question
Can an unprivileged attacker who submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan, controlling the ABI encoding of the wrapped Bridge argument, drive `create_rpc_context` in `crates/sequencer/src/rpc.rs` so that the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching, breaking the invariant that system transactions never collide with user transactions in nonce space?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `create_rpc_context`
- Entrypoint: unprivileged party submits a deposit blob referencing a Bitcoin transaction it can still replace or orphan
- Attacker controls: the ABI encoding of the wrapped Bridge argument
- Exploit idea: system-signer nonce coupling - reach `create_rpc_context` from that entrypoint and force the divergence where the nonce the deposit system transaction is built with and the nonce the EVM expects for `SYSTEM_SIGNER` stop matching; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `register_rpc_methods`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: system transactions never collide with user transactions in nonce space
- Expected Immunefi impact: Critical - network unable to confirm new transactions (settlement halt) triggered by attacker-shaped protocol data
- Fast validation: interleave user transactions with deposits and assert every system transaction executes
