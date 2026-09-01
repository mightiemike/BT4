# Q0273: auth param stripping via `remove_last_param` (auth.rs)

## Question
Can an unprivileged attacker who appends an extra positional parameter to a JSON-RPC request, controlling the method name and parameter shape, drive `remove_last_param` in `crates/common/src/rpc/auth.rs` so that the parameter list the method finally receives and the parameter list the caller intended stop being the same list, breaking the invariant that `Auth` never alters the semantics of an unprotected method?

## Target
- File/function: `crates/common/src/rpc/auth.rs` -> `remove_last_param`
- Entrypoint: unprivileged party appends an extra positional parameter to a JSON-RPC request
- Attacker controls: the method name and parameter shape
- Exploit idea: auth param stripping - reach `remove_last_param` from that entrypoint and force the divergence where the parameter list the method finally receives and the parameter list the caller intended stop being the same list; the adjacent symbols in the same file that carry the value are `Auth`, `call`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: `Auth` never alters the semantics of an unprotected method
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: call an unprotected method with a trailing param and assert it is not silently consumed
