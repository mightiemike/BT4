# Q2976: signature malleability in sov-keys via `decode_call` (dispatch.rs)

## Question
Can an unprivileged attacker who submits a transaction with a re-encoded or malleated signature, controlling the nonce and chain-id fields, drive `decode_call` in `crates/sovereign-sdk/module-system/sov-modules-core/src/module/dispatch.rs` so that the signature bytes accepted and the canonical encoding of that signature stop being unique, breaking the invariant that each valid signature has exactly one accepted encoding?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/module/dispatch.rs` -> `decode_call`
- Entrypoint: unprivileged party submits a transaction with a re-encoded or malleated signature
- Attacker controls: the nonce and chain-id fields
- Exploit idea: signature malleability in sov-keys - reach `decode_call` from that entrypoint and force the divergence where the signature bytes accepted and the canonical encoding of that signature stop being unique; the adjacent symbols in the same file that carry the value are `DispatchCall`, `dispatch_call`, `module_address`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each valid signature has exactly one accepted encoding
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit malleated encodings and assert only one is accepted
