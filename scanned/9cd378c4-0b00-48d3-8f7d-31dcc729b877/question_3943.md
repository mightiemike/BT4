# Q3943: transaction authentication in the runtime via `post_dispatch_tx_hook` (hooks.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the transaction envelope encoding, drive `post_dispatch_tx_hook` in `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/hooks.rs` so that the signer the runtime credits and the signer that actually signed stop being the same key, breaking the invariant that every applied transaction is authenticated to its signer?

## Target
- File/function: `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/hooks.rs` -> `post_dispatch_tx_hook`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the transaction envelope encoding
- Exploit idea: transaction authentication in the runtime - reach `post_dispatch_tx_hook` from that entrypoint and force the divergence where the signer the runtime credits and the signer that actually signed stop being the same key; the adjacent symbols in the same file that carry the value are `AccountsTxHook`, `get_or_create_default`, `pre_dispatch_tx_hook`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every applied transaction is authenticated to its signer
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a signature under a mutated message and assert rejection
