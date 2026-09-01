# Q1219: nonce/account hook bypass via `try_from` (default_signature.rs)

## Question
Can an unprivileged attacker who submits a transaction with a re-encoded or malleated signature, controlling signature encoding and recovery bytes, drive `try_from` in `crates/sovereign-sdk/module-system/sov-keys/src/default_signature.rs` so that the nonce the accounts module increments and the nonce the transaction declared stop being equal, breaking the invariant that each transaction consumes exactly its declared nonce once?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-keys/src/default_signature.rs` -> `try_from`
- Entrypoint: unprivileged party submits a transaction with a re-encoded or malleated signature
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: nonce/account hook bypass - reach `try_from` from that entrypoint and force the divergence where the nonce the accounts module increments and the nonce the transaction declared stop being equal; the adjacent symbols in the same file that carry the value are `SigVerificationError`, `K256PrivateKey`, `K256PublicKey`, `K256Signature`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each transaction consumes exactly its declared nonce once
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a transaction and assert the second application fails
