# Q5248: nonce/account hook bypass via `pre_dispatch_tx_hook` (hooks.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the transaction envelope encoding, drive `pre_dispatch_tx_hook` in `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` so that the nonce the accounts module increments and the nonce the transaction declared stop being equal, breaking the invariant that each transaction consumes exactly its declared nonce once?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` -> `pre_dispatch_tx_hook`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the transaction envelope encoding
- Exploit idea: nonce/account hook bypass - reach `pre_dispatch_tx_hook` from that entrypoint and force the divergence where the nonce the accounts module increments and the nonce the transaction declared stop being equal; the adjacent symbols in the same file that carry the value are `TxHooks`, `ApplyL2BlockHooks`, `HookL2BlockInfo`, `SlotHooks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each transaction consumes exactly its declared nonce once
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a transaction and assert the second application fails
