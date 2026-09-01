# Q1973: auth param stripping via `start_rpc_server` (server.rs)

## Question
Can an unprivileged attacker who calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter, controlling an extra trailing JSON-RPC parameter, drive `start_rpc_server` in `crates/common/src/rpc/server.rs` so that the parameter list the method finally receives and the parameter list the caller intended stop being the same list, breaking the invariant that `Auth` never alters the semantics of an unprotected method?

## Target
- File/function: `crates/common/src/rpc/server.rs` -> `start_rpc_server`
- Entrypoint: unprivileged party calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter
- Attacker controls: an extra trailing JSON-RPC parameter
- Exploit idea: auth param stripping - reach `start_rpc_server` from that entrypoint and force the divergence where the parameter list the method finally receives and the parameter list the caller intended stop being the same list; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: `Auth` never alters the semantics of an unprotected method
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: call an unprotected method with a trailing param and assert it is not silently consumed
