# Q3060: signature malleability in sov-keys via `module_address` (dispatch.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the transaction envelope encoding, drive `module_address` in `crates/sovereign-sdk/module-system/sov-modules-core/src/module/dispatch.rs` so that the signature bytes accepted and the canonical encoding of that signature stop being unique, breaking the invariant that each valid signature has exactly one accepted encoding?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/module/dispatch.rs` -> `module_address`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the transaction envelope encoding
- Exploit idea: signature malleability in sov-keys - reach `module_address` from that entrypoint and force the divergence where the signature bytes accepted and the canonical encoding of that signature stop being unique; the adjacent symbols in the same file that carry the value are `DispatchCall`, `decode_call`, `dispatch_call`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each valid signature has exactly one accepted encoding
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit malleated encodings and assert only one is accepted
