# Q2153: auth param stripping via `internal_rpc_error` (utils.rs)

## Question
Can an unprivileged attacker who appends an extra positional parameter to a JSON-RPC request, controlling an extra trailing JSON-RPC parameter, drive `internal_rpc_error` in `crates/common/src/rpc/utils.rs` so that the parameter list the method finally receives and the parameter list the caller intended stop being the same list, breaking the invariant that `Auth` never alters the semantics of an unprotected method?

## Target
- File/function: `crates/common/src/rpc/utils.rs` -> `internal_rpc_error`
- Entrypoint: unprivileged party appends an extra positional parameter to a JSON-RPC request
- Attacker controls: an extra trailing JSON-RPC parameter
- Exploit idea: auth param stripping - reach `internal_rpc_error` from that entrypoint and force the divergence where the parameter list the method finally receives and the parameter list the caller intended stop being the same list; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: `Auth` never alters the semantics of an unprotected method
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: call an unprotected method with a trailing param and assert it is not silently consumed
