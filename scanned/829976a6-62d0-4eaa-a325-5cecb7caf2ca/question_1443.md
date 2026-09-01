# Q1443: protected-method surface gap via `get_healthcheck_proxy_layer` (mod.rs)

## Question
Can an unprivileged attacker who calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter, controlling an extra trailing JSON-RPC parameter, drive `get_healthcheck_proxy_layer` in `crates/common/src/rpc/mod.rs` so that the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set, breaking the invariant that every state-mutating RPC requires the API key?

## Target
- File/function: `crates/common/src/rpc/mod.rs` -> `get_healthcheck_proxy_layer`
- Entrypoint: unprivileged party calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter
- Attacker controls: an extra trailing JSON-RPC parameter
- Exploit idea: protected-method surface gap - reach `get_healthcheck_proxy_layer` from that entrypoint and force the divergence where the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set; the adjacent symbols in the same file that carry the value are `Logger`, `register_healthcheck_rpc`, `register_healthcheck_rpc_light_client_prover`, `get_cors_layer`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every state-mutating RPC requires the API key
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: enumerate registered methods and assert each mutating one is protected
