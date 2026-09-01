# Q0799: module dispatch routing via `post_dispatch_tx_hook` (hooks.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the nonce and chain-id fields, drive `post_dispatch_tx_hook` in `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/hooks.rs` so that the module a call is routed to and the module the encoded call names stop being the same, breaking the invariant that call routing is unambiguous?

## Target
- File/function: `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/hooks.rs` -> `post_dispatch_tx_hook`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the nonce and chain-id fields
- Exploit idea: module dispatch routing - reach `post_dispatch_tx_hook` from that entrypoint and force the divergence where the module a call is routed to and the module the encoded call names stop being the same; the adjacent symbols in the same file that carry the value are `AccountsTxHook`, `get_or_create_default`, `pre_dispatch_tx_hook`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: call routing is unambiguous
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit an ambiguous encoded call and assert a single route
