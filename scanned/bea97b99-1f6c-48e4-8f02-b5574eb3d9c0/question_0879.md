# Q0879: transaction authentication in the runtime via `from_iter` (lib.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the nonce and chain-id fields, drive `from_iter` in `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/lib.rs` so that the signer the runtime credits and the signer that actually signed stop being the same key, breaking the invariant that every applied transaction is authenticated to its signer?

## Target
- File/function: `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/lib.rs` -> `from_iter`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the nonce and chain-id fields
- Exploit idea: transaction authentication in the runtime - reach `from_iter` from that entrypoint and force the divergence where the signer the runtime credits and the signer that actually signed stop being the same key; the adjacent symbols in the same file that carry the value are `Account`, `Accounts`, `encode_value`, `try_decode_value`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every applied transaction is authenticated to its signer
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a signature under a mutated message and assert rejection
