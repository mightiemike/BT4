# Q4543: nonce/account hook bypass via `decode_call` (dispatch.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the nonce and chain-id fields, drive `decode_call` in `crates/sovereign-sdk/module-system/sov-modules-core/src/module/dispatch.rs` so that the nonce the accounts module increments and the nonce the transaction declared stop being equal, breaking the invariant that each transaction consumes exactly its declared nonce once?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/module/dispatch.rs` -> `decode_call`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the nonce and chain-id fields
- Exploit idea: nonce/account hook bypass - reach `decode_call` from that entrypoint and force the divergence where the nonce the accounts module increments and the nonce the transaction declared stop being equal; the adjacent symbols in the same file that carry the value are `DispatchCall`, `dispatch_call`, `module_address`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each transaction consumes exactly its declared nonce once
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: replay a transaction and assert the second application fails
