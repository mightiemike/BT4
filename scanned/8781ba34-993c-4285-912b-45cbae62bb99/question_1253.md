# Q1253: auth param stripping via `register_healthcheck_rpc_light_client_prover` (mod.rs)

## Question
Can an unprivileged attacker who calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter, controlling the method name and parameter shape, drive `register_healthcheck_rpc_light_client_prover` in `crates/common/src/rpc/mod.rs` so that the parameter list the method finally receives and the parameter list the caller intended stop being the same list, breaking the invariant that `Auth` never alters the semantics of an unprotected method?

## Target
- File/function: `crates/common/src/rpc/mod.rs` -> `register_healthcheck_rpc_light_client_prover`
- Entrypoint: unprivileged party calls a node method that is not in `PROTECTED_METHODS` with a trailing parameter
- Attacker controls: the method name and parameter shape
- Exploit idea: auth param stripping - reach `register_healthcheck_rpc_light_client_prover` from that entrypoint and force the divergence where the parameter list the method finally receives and the parameter list the caller intended stop being the same list; the adjacent symbols in the same file that carry the value are `Logger`, `register_healthcheck_rpc`, `get_healthcheck_proxy_layer`, `get_cors_layer`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: `Auth` never alters the semantics of an unprotected method
- Expected Immunefi impact: High - unauthenticated RPC mutating node state or bypassing `Auth`
- Fast validation: call an unprotected method with a trailing param and assert it is not silently consumed
