# Q0071: transaction authentication in the runtime via `rpc_methods` (runtime.rs)

## Question
Can an unprivileged attacker who submits a transaction with a re-encoded or malleated signature, controlling signature encoding and recovery bytes, drive `rpc_methods` in `crates/citrea-stf/src/runtime.rs` so that the signer the runtime credits and the signer that actually signed stop being the same key, breaking the invariant that every applied transaction is authenticated to its signer?

## Target
- File/function: `crates/citrea-stf/src/runtime.rs` -> `rpc_methods`
- Entrypoint: unprivileged party submits a transaction with a re-encoded or malleated signature
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: transaction authentication in the runtime - reach `rpc_methods` from that entrypoint and force the divergence where the signer the runtime credits and the signer that actually signed stop being the same key; the adjacent symbols in the same file that carry the value are `CitreaRuntime`, `genesis_config`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every applied transaction is authenticated to its signer
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a signature under a mutated message and assert rejection
