# Q5766: read/write log ordering via `is_empty` (mod.rs)

## Question
Can an unprivileged attacker who sends a transaction that reads state written earlier in the same block by another of its transactions, controlling intra-block ordering of its own transactions, drive `is_empty` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/mod.rs` so that the `ReadWriteLog` order the native run produced and the order the guest consumes stop being the same order, breaking the invariant that log ordering is canonical and replay-stable?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/mod.rs` -> `is_empty`
- Entrypoint: unprivileged party sends a transaction that reads state written earlier in the same block by another of its transactions
- Attacker controls: intra-block ordering of its own transactions
- Exploit idea: read/write log ordering - reach `is_empty` from that entrypoint and force the divergence where the `ReadWriteLog` order the native run produced and the order the guest consumes stop being the same order; the adjacent symbols in the same file that carry the value are `StorageKey`, `StorageValue`, `StorageProof`, `Storage`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: log ordering is canonical and replay-stable
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: shuffle log order in a replay and assert rejection
