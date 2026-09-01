# Q4080: ledger RPC index bounds via `register_rpc_methods` (rpc.rs)

## Question
Can an unprivileged attacker who asks a node for a proof or commitment it has stored but not yet verified, controlling the height or index requested, drive `register_rpc_methods` in `crates/light-client-prover/src/rpc.rs` so that the range the ledger RPC iterates and the range the caller requested stop being the same range, breaking the invariant that range queries never read beyond the requested window?

## Target
- File/function: `crates/light-client-prover/src/rpc.rs` -> `register_rpc_methods`
- Entrypoint: unprivileged party asks a node for a proof or commitment it has stored but not yet verified
- Attacker controls: the height or index requested
- Exploit idea: ledger RPC index bounds - reach `register_rpc_methods` from that entrypoint and force the divergence where the range the ledger RPC iterates and the range the caller requested stop being the same range; the adjacent symbols in the same file that carry the value are `RpcContext`, `LightClientProverRpc`, `LightClientProverRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: range queries never read beyond the requested window
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: call with reversed/oversized ranges and assert bounded output
