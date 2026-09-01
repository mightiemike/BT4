# Q3004: module dispatch routing via `dispatch_call` (dispatch.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling signature encoding and recovery bytes, drive `dispatch_call` in `crates/sovereign-sdk/module-system/sov-modules-core/src/module/dispatch.rs` so that the module a call is routed to and the module the encoded call names stop being the same, breaking the invariant that call routing is unambiguous?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/module/dispatch.rs` -> `dispatch_call`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: module dispatch routing - reach `dispatch_call` from that entrypoint and force the divergence where the module a call is routed to and the module the encoded call names stop being the same; the adjacent symbols in the same file that carry the value are `DispatchCall`, `decode_call`, `module_address`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: call routing is unambiguous
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit an ambiguous encoded call and assert a single route
