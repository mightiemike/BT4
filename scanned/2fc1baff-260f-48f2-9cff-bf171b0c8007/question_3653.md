# Q3653: JMT version bookkeeping via `init_version` (prover_storage.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key in one block, controlling intra-block ordering of its own transactions, drive `init_version` in `crates/sovereign-sdk/module-system/sov-state/src/prover_storage.rs` so that the storage version a write is recorded under and the version the root is computed for stop being the same, breaking the invariant that each root corresponds to exactly one version?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/prover_storage.rs` -> `init_version`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key in one block
- Attacker controls: intra-block ordering of its own transactions
- Exploit idea: JMT version bookkeeping - reach `init_version` from that entrypoint and force the divergence where the storage version a write is recorded under and the version the root is computed for stop being the same; the adjacent symbols in the same file that carry the value are `ProverStorage`, `ProverStateUpdate`, `committable_latest_version`, `uncommittable_with_version`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each root corresponds to exactly one version
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: interleave versions and diff computed roots
