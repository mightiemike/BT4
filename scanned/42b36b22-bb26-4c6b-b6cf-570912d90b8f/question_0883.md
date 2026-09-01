# Q0883: protected-method surface gap via `calculate_timeout_ms` (eip_7966.rs)

## Question
Can an unprivileged attacker who appends an extra positional parameter to a JSON-RPC request, controlling an extra trailing JSON-RPC parameter, drive `calculate_timeout_ms` in `crates/common/src/rpc/eip_7966.rs` so that the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set, breaking the invariant that every state-mutating RPC requires the API key?

## Target
- File/function: `crates/common/src/rpc/eip_7966.rs` -> `calculate_timeout_ms`
- Entrypoint: unprivileged party appends an extra positional parameter to a JSON-RPC request
- Attacker controls: an extra trailing JSON-RPC parameter
- Exploit idea: protected-method surface gap - reach `calculate_timeout_ms` from that entrypoint and force the divergence where the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set; the adjacent symbols in the same file that carry the value are `timeout_error`, `unready_error`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every state-mutating RPC requires the API key
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: enumerate registered methods and assert each mutating one is protected
