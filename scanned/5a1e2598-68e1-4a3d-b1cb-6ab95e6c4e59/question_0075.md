# Q0075: protected-method surface gap via `call` (auth.rs)

## Question
Can an unprivileged attacker who calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter, controlling the method name and parameter shape, drive `call` in `crates/common/src/rpc/auth.rs` so that the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set, breaking the invariant that every state-mutating RPC requires the API key?

## Target
- File/function: `crates/common/src/rpc/auth.rs` -> `call`
- Entrypoint: unprivileged party calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter
- Attacker controls: the method name and parameter shape
- Exploit idea: protected-method surface gap - reach `call` from that entrypoint and force the divergence where the set of node-mutating methods and the set enumerated in `PROTECTED_METHODS` stop being the same set; the adjacent symbols in the same file that carry the value are `Auth`, `remove_last_param`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every state-mutating RPC requires the API key
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: enumerate registered methods and assert each mutating one is protected
