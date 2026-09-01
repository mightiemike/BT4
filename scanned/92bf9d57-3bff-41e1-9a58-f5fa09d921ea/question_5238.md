# Q5238: transaction authentication in the runtime via `post_dispatch_tx_hook` (hooks.rs)

## Question
Can an unprivileged attacker who submits a transaction with a re-encoded or malleated signature, controlling signature encoding and recovery bytes, drive `post_dispatch_tx_hook` in `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` so that the signer the runtime credits and the signer that actually signed stop being the same key, breaking the invariant that every applied transaction is authenticated to its signer?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` -> `post_dispatch_tx_hook`
- Entrypoint: unprivileged party submits a transaction with a re-encoded or malleated signature
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: transaction authentication in the runtime - reach `post_dispatch_tx_hook` from that entrypoint and force the divergence where the signer the runtime credits and the signer that actually signed stop being the same key; the adjacent symbols in the same file that carry the value are `TxHooks`, `ApplyL2BlockHooks`, `HookL2BlockInfo`, `SlotHooks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every applied transaction is authenticated to its signer
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a signature under a mutated message and assert rejection
