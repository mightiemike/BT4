# Q1439: module dispatch routing via `pub_key` (default_signature.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the nonce and chain-id fields, drive `pub_key` in `crates/sovereign-sdk/module-system/sov-keys/src/default_signature.rs` so that the module a call is routed to and the module the encoded call names stop being the same, breaking the invariant that call routing is unambiguous?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-keys/src/default_signature.rs` -> `pub_key`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the nonce and chain-id fields
- Exploit idea: module dispatch routing - reach `pub_key` from that entrypoint and force the divergence where the module a call is routed to and the module the encoded call names stop being the same; the adjacent symbols in the same file that carry the value are `SigVerificationError`, `K256PrivateKey`, `K256PublicKey`, `K256Signature`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: call routing is unambiguous
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit an ambiguous encoded call and assert a single route
