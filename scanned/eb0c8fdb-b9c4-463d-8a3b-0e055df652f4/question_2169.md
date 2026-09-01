# Q2169: signature malleability in sov-keys via `pre_dispatch_tx_hook` (hooks.rs)

## Question
Can an unprivileged attacker who submits a transaction with a re-encoded or malleated signature, controlling signature encoding and recovery bytes, drive `pre_dispatch_tx_hook` in `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` so that the signature bytes accepted and the canonical encoding of that signature stop being unique, breaking the invariant that each valid signature has exactly one accepted encoding?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-api/src/hooks.rs` -> `pre_dispatch_tx_hook`
- Entrypoint: unprivileged party submits a transaction with a re-encoded or malleated signature
- Attacker controls: signature encoding and recovery bytes
- Exploit idea: signature malleability in sov-keys - reach `pre_dispatch_tx_hook` from that entrypoint and force the divergence where the signature bytes accepted and the canonical encoding of that signature stop being unique; the adjacent symbols in the same file that carry the value are `TxHooks`, `ApplyL2BlockHooks`, `HookL2BlockInfo`, `SlotHooks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each valid signature has exactly one accepted encoding
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: submit malleated encodings and assert only one is accepted
