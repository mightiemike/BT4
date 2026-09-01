# Q2526: module dispatch routing via `pre_state_root` (hooks.rs)

## Question
Can an unprivileged attacker who submits a transaction with a re-encoded or malleated signature, controlling the transaction envelope encoding, drive `pre_state_root` in `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` so that the module a call is routed to and the module the encoded call names stop being the same, breaking the invariant that call routing is unambiguous?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` -> `pre_state_root`
- Entrypoint: unprivileged party submits a transaction with a re-encoded or malleated signature
- Attacker controls: the transaction envelope encoding
- Exploit idea: module dispatch routing - reach `pre_state_root` from that entrypoint and force the divergence where the module a call is routed to and the module the encoded call names stop being the same; the adjacent symbols in the same file that carry the value are `TxHooks`, `ApplyL2BlockHooks`, `HookL2BlockInfo`, `SlotHooks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: call routing is unambiguous
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit an ambiguous encoded call and assert a single route
