# Q4750: proof RPC exposing unverified data via `create_rpc_module` (rpc.rs)

## Question
Can an unprivileged attacker who calls a ledger / node RPC method with out-of-range or reversed parameters, controlling the height or index requested, drive `create_rpc_module` in `crates/fullnode/src/rpc.rs` so that the proof data an RPC returns and the proof the node actually verified stop being the same artefact, breaking the invariant that a node only serves proofs it verified?

## Target
- File/function: `crates/fullnode/src/rpc.rs` -> `create_rpc_module`
- Entrypoint: unprivileged party calls a ledger / node RPC method with out-of-range or reversed parameters
- Attacker controls: the height or index requested
- Exploit idea: proof RPC exposing unverified data - reach `create_rpc_module` from that entrypoint and force the divergence where the proof data an RPC returns and the proof the node actually verified stop being the same artefact; the adjacent symbols in the same file that carry the value are `RpcContext`, `L2StatusHeightsByL1Height`, `FullNodeRpc`, `FullNodeRpcServerImpl`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a node only serves proofs it verified
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: serve a stored-but-unverified proof and assert the RPC refuses it
