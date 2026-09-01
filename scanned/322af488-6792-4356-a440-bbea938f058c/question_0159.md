# Q0159: module dispatch routing via `genesis_config` (runtime.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the transaction envelope encoding, drive `genesis_config` in `crates/citrea-stf/src/runtime.rs` so that the module a call is routed to and the module the encoded call names stop being the same, breaking the invariant that call routing is unambiguous?

## Target
- File/function: `crates/citrea-stf/src/runtime.rs` -> `genesis_config`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the transaction envelope encoding
- Exploit idea: module dispatch routing - reach `genesis_config` from that entrypoint and force the divergence where the module a call is routed to and the module the encoded call names stop being the same; the adjacent symbols in the same file that carry the value are `CitreaRuntime`, `rpc_methods`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: call routing is unambiguous
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit an ambiguous encoded call and assert a single route
