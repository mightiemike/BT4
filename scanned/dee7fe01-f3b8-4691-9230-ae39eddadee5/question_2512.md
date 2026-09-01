# Q2512: ledger RPC index bounds via `get_light_client_proof_by_l1_height` (rpc.rs)

## Question
Can an unprivileged attacker who asks a node for a proof or commitment it has stored but not yet verified, controlling range and index parameters, drive `get_light_client_proof_by_l1_height` in `crates/light-client-prover/src/rpc.rs` so that the range the ledger RPC iterates and the range the caller requested stop being the same range, breaking the invariant that range queries never read beyond the requested window?

## Target
- File/function: `crates/light-client-prover/src/rpc.rs` -> `get_light_client_proof_by_l1_height`
- Entrypoint: unprivileged party asks a node for a proof or commitment it has stored but not yet verified
- Attacker controls: range and index parameters
- Exploit idea: ledger RPC index bounds - reach `get_light_client_proof_by_l1_height` from that entrypoint and force the divergence where the range the ledger RPC iterates and the range the caller requested stop being the same range; the adjacent symbols in the same file that carry the value are `RpcContext`, `LightClientProverRpc`, `LightClientProverRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: range queries never read beyond the requested window
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: call with reversed/oversized ranges and assert bounded output
