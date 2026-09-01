# Q1263: protected-method surface gap via `register_healthcheck_rpc_light_client_prover` (mod.rs)

## Question
Can an unprivileged attacker who appends an extra positional parameter to a JSON-RPC request, controlling the method name and parameter shape, drive `register_healthcheck_rpc_light_client_prover` in `crates/common/src/rpc/mod.rs` so that the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set, breaking the invariant that every state-mutating RPC requires the API key?

## Target
- File/function: `crates/common/src/rpc/mod.rs` -> `register_healthcheck_rpc_light_client_prover`
- Entrypoint: unprivileged party appends an extra positional parameter to a JSON-RPC request
- Attacker controls: the method name and parameter shape
- Exploit idea: protected-method surface gap - reach `register_healthcheck_rpc_light_client_prover` from that entrypoint and force the divergence where the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set; the adjacent symbols in the same file that carry the value are `Logger`, `register_healthcheck_rpc`, `get_healthcheck_proxy_layer`, `get_cors_layer`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every state-mutating RPC requires the API key
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: enumerate registered methods and assert each mutating one is protected
