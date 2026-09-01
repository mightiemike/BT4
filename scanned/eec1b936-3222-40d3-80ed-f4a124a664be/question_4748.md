# Q4748: read/write log ordering via `compute_state_update` (prover_storage.rs)

## Question
Can an unprivileged attacker who sends a transaction that reads state written earlier in the same block by another of its transactions, controlling which JMT keys are read and written, drive `compute_state_update` in `crates/sovereign-sdk/module-system/sov-state/src/prover_storage.rs` so that the `ReadWriteLog` order the native run produced and the order the guest consumes stop being the same order, breaking the invariant that log ordering is canonical and replay-stable?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/prover_storage.rs` -> `compute_state_update`
- Entrypoint: unprivileged party sends a transaction that reads state written earlier in the same block by another of its transactions
- Attacker controls: which JMT keys are read and written
- Exploit idea: read/write log ordering - reach `compute_state_update` from that entrypoint and force the divergence where the `ReadWriteLog` order the native run produced and the order the guest consumes stop being the same order; the adjacent symbols in the same file that carry the value are `ProverStorage`, `ProverStateUpdate`, `committable_latest_version`, `uncommittable_with_version`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: log ordering is canonical and replay-stable
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: shuffle log order in a replay and assert rejection
