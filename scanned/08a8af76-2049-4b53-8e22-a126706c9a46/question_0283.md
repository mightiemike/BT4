# Q0283: protected-method surface gap via `remove_last_param` (auth.rs)

## Question
Can an unprivileged attacker who calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter, controlling an extra trailing JSON-RPC parameter, drive `remove_last_param` in `crates/common/src/rpc/auth.rs` so that the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set, breaking the invariant that every state-mutating RPC requires the API key?

## Target
- File/function: `crates/common/src/rpc/auth.rs` -> `remove_last_param`
- Entrypoint: unprivileged party calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter
- Attacker controls: an extra trailing JSON-RPC parameter
- Exploit idea: protected-method surface gap - reach `remove_last_param` from that entrypoint and force the divergence where the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set; the adjacent symbols in the same file that carry the value are `Auth`, `call`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every state-mutating RPC requires the API key
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: enumerate registered methods and assert each mutating one is protected
