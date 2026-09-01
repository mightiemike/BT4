# Q0729: signature malleability in sov-keys via `pre_dispatch_tx_hook` (hooks.rs)

## Question
Can an unprivileged attacker who replays a previously applied transaction with an altered envelope, controlling the transaction envelope encoding, drive `pre_dispatch_tx_hook` in `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/hooks.rs` so that the signature bytes accepted and the canonical encoding of that signature stop being unique, breaking the invariant that each valid signature has exactly one accepted encoding?

## Target
- File/function: `crates/sovereign-sdk/module-system/module-implementations/sov-accounts/src/hooks.rs` -> `pre_dispatch_tx_hook`
- Entrypoint: unprivileged party replays a previously applied transaction with an altered envelope
- Attacker controls: the transaction envelope encoding
- Exploit idea: signature malleability in sov-keys - reach `pre_dispatch_tx_hook` from that entrypoint and force the divergence where the signature bytes accepted and the canonical encoding of that signature stop being unique; the adjacent symbols in the same file that carry the value are `AccountsTxHook`, `get_or_create_default`, `post_dispatch_tx_hook`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each valid signature has exactly one accepted encoding
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit malleated encodings and assert only one is accepted
