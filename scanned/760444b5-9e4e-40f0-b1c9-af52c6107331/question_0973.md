# Q0973: proof served to a bridge consumer via `get_proving_session_infos` (rpc.rs)

## Question
Can an unprivileged attacker who calls a ledger / node RPC method with out-of-range or reversed parameters, controlling the height or index requested, drive `get_proving_session_infos` in `crates/batch-prover/src/rpc.rs` so that the state root an RPC reports as final and the root a verified proof commits stop being the same, breaking the invariant that finality reported over RPC is proof-backed?

## Target
- File/function: `crates/batch-prover/src/rpc.rs` -> `get_proving_session_infos`
- Entrypoint: unprivileged party calls a ledger / node RPC method with out-of-range or reversed parameters
- Attacker controls: the height or index requested
- Exploit idea: proof served to a bridge consumer - reach `get_proving_session_infos` from that entrypoint and force the divergence where the state root an RPC reports as final and the root a verified proof commits stop being the same; the adjacent symbols in the same file that carry the value are `ProverInputResponse`, `ProvingJobResponse`, `ProvingSessionInfoResponse`, `RpcContext`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: finality reported over RPC is proof-backed
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: report finality before proof verification and assert refusal
