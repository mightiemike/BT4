# Q2079: module dispatch routing via `from_str` (default_signature.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling signature encoding and recovery bytes, drive `from_str` in `crates/sovereign-sdk/module-system/sov-keys/src/default_signature.rs` so that the module a call is routed to and the module the encoded call names stop being the same, breaking the invariant that call routing is unambiguous?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-keys/src/default_signature.rs` -> `from_str`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: module dispatch routing - reach `from_str` from that entrypoint and force the divergence where the module a call is routed to and the module the encoded call names stop being the same; the adjacent symbols in the same file that carry the value are `SigVerificationError`, `K256PrivateKey`, `K256PublicKey`, `K256Signature`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: call routing is unambiguous
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit an ambiguous encoded call and assert a single route
