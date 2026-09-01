# Q4035: proof served to a bridge consumer via `get_state_diff_by_block_number` (rpc.rs)

## Question
Can an unprivileged attacker who asks a node for a proof or commitment it has stored but not yet verified, controlling the height or index requested, drive `get_state_diff_by_block_number` in `crates/fullnode/src/rpc.rs` so that the state root an RPC reports as final and the root a verified proof commits stop being the same, breaking the invariant that finality reported over RPC is proof-backed?

## Target
- File/function: `crates/fullnode/src/rpc.rs` -> `get_state_diff_by_block_number`
- Entrypoint: unprivileged party asks a node for a proof or commitment it has stored but not yet verified
- Attacker controls: the height or index requested
- Exploit idea: proof served to a bridge consumer - reach `get_state_diff_by_block_number` from that entrypoint and force the divergence where the state root an RPC reports as final and the root a verified proof commits stop being the same; the adjacent symbols in the same file that carry the value are `RpcContext`, `L2StatusHeightsByL1Height`, `FullNodeRpc`, `FullNodeRpcServerImpl`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: finality reported over RPC is proof-backed
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: report finality before proof verification and assert refusal
