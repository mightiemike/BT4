# Q4085: proof RPC exposing unverified data via `get_batch_proof_method_ids` (rpc.rs)

## Question
Can an unprivileged attacker who asks a node for a proof or commitment it has stored but not yet verified, controlling range and index parameters, drive `get_batch_proof_method_ids` in `crates/light-client-prover/src/rpc.rs` so that the proof data an RPC returns and the proof the node actually verified stop being the same artefact, breaking the invariant that a node only serves proofs it verified?

## Target
- File/function: `crates/light-client-prover/src/rpc.rs` -> `get_batch_proof_method_ids`
- Entrypoint: unprivileged party asks a node for a proof or commitment it has stored but not yet verified
- Attacker controls: range and index parameters
- Exploit idea: proof RPC exposing unverified data - reach `get_batch_proof_method_ids` from that entrypoint and force the divergence where the proof data an RPC returns and the proof the node actually verified stop being the same artefact; the adjacent symbols in the same file that carry the value are `RpcContext`, `LightClientProverRpc`, `LightClientProverRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a node only serves proofs it verified
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: serve a stored-but-unverified proof and assert the RPC refuses it
