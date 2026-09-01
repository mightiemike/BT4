# Q4343: transaction authentication in the runtime via `end_l2_block_hook` (hooks.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the transaction envelope encoding, drive `end_l2_block_hook` in `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` so that the signer the runtime credits and the signer that actually signed stop being the same key, breaking the invariant that every applied transaction is authenticated to its signer?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` -> `end_l2_block_hook`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the transaction envelope encoding
- Exploit idea: transaction authentication in the runtime - reach `end_l2_block_hook` from that entrypoint and force the divergence where the signer the runtime credits and the signer that actually signed stop being the same key; the adjacent symbols in the same file that carry the value are `TxHooks`, `ApplyL2BlockHooks`, `HookL2BlockInfo`, `SlotHooks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every applied transaction is authenticated to its signer
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a signature under a mutated message and assert rejection
