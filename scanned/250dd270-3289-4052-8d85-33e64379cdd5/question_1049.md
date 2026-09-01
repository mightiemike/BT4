# Q1049: signature malleability in sov-keys via `try_decode_value` (lib.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling signature encoding and recovery bytes, drive `try_decode_value` in `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/lib.rs` so that the signature bytes accepted and the canonical encoding of that signature stop being unique, breaking the invariant that each valid signature has exactly one accepted encoding?

## Target
- File/function: `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/lib.rs` -> `try_decode_value`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: signature malleability in sov-keys - reach `try_decode_value` from that entrypoint and force the divergence where the signature bytes accepted and the canonical encoding of that signature stop being unique; the adjacent symbols in the same file that carry the value are `Account`, `Accounts`, `from_iter`, `encode_value`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each valid signature has exactly one accepted encoding
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit malleated encodings and assert only one is accepted
