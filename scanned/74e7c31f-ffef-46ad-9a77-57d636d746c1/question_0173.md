# Q0173: proof served to a bridge consumer via `register_rpc_methods` (rpc.rs)

## Question
Can an unprivileged attacker who asks a node for a proof or commitment it has stored but not yet verified, controlling range and index parameters, drive `register_rpc_methods` in `crates/batch-prover/src/rpc.rs` so that the state root an RPC reports as final and the root a verified proof commits stop being the same, breaking the invariant that finality reported over RPC is proof-backed?

## Target
- File/function: `crates/batch-prover/src/rpc.rs` -> `register_rpc_methods`
- Entrypoint: unprivileged party asks a node for a proof or commitment it has stored but not yet verified
- Attacker controls: range and index parameters
- Exploit idea: proof served to a bridge consumer - reach `register_rpc_methods` from that entrypoint and force the divergence where the state root an RPC reports as final and the root a verified proof commits stop being the same; the adjacent symbols in the same file that carry the value are `ProverInputResponse`, `ProvingJobResponse`, `ProvingSessionInfoResponse`, `RpcContext`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: finality reported over RPC is proof-backed
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: report finality before proof verification and assert refusal
