# Q5043: transaction authentication in the runtime via `try_from` (default_signature.rs)

## Question
Can an unprivileged attacker who submits a transaction with a re-encoded or malleated signature, controlling the nonce and chain-id fields, drive `try_from` in `crates/sovereign-sdk/module-system/sov-keys/src/default_signature.rs` so that the signer the runtime credits and the signer that actually signed stop being the same key, breaking the invariant that every applied transaction is authenticated to its signer?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-keys/src/default_signature.rs` -> `try_from`
- Entrypoint: unprivileged party submits a transaction with a re-encoded or malleated signature
- Attacker controls: the nonce and chain-id fields
- Exploit idea: transaction authentication in the runtime - reach `try_from` from that entrypoint and force the divergence where the signer the runtime credits and the signer that actually signed stop being the same key; the adjacent symbols in the same file that carry the value are `SigVerificationError`, `K256PrivateKey`, `K256PublicKey`, `K256Signature`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every applied transaction is authenticated to its signer
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a signature under a mutated message and assert rejection
