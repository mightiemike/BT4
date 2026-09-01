# Q1693: block-by-hash for a non-canonical block via `create_rpc_module` (rpc.rs)

## Question
Can an unprivileged attacker who asks a node for a proof or commitment it has stored but not yet verified, controlling the height or index requested, drive `create_rpc_module` in `crates/fullnode/src/rpc.rs` so that the block a hash lookup returns and the canonical block at that height stop being the same block, breaking the invariant that hash lookups never surface orphaned state as canonical?

## Target
- File/function: `crates/fullnode/src/rpc.rs` -> `create_rpc_module`
- Entrypoint: unprivileged party asks a node for a proof or commitment it has stored but not yet verified
- Attacker controls: the height or index requested
- Exploit idea: block-by-hash for a non-canonical block - reach `create_rpc_module` from that entrypoint and force the divergence where the block a hash lookup returns and the canonical block at that height stop being the same block; the adjacent symbols in the same file that carry the value are `RpcContext`, `L2StatusHeightsByL1Height`, `FullNodeRpc`, `FullNodeRpcServerImpl`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: hash lookups never surface orphaned state as canonical
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: insert a non-canonical block and assert lookups mark it as such
