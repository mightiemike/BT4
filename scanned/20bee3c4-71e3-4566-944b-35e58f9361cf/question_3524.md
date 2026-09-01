# Q3524: delete-then-read semantics via `read_value` (prover_storage.rs)

## Question
Can an unprivileged attacker who sends an L2 transaction whose execution touches a JMT slot no other transaction touches, controlling intra-block ordering of its own transactions, drive `read_value` in `crates/sovereign-sdk/module-system/sov-state/src/prover_storage.rs` so that the value observed after a delete in-block and the value the JMT commits stop being consistent, breaking the invariant that deletes are visible identically natively and in-circuit?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/prover_storage.rs` -> `read_value`
- Entrypoint: unprivileged party sends an L2 transaction whose execution touches a JMT slot no other transaction touches
- Attacker controls: intra-block ordering of its own transactions
- Exploit idea: delete-then-read semantics - reach `read_value` from that entrypoint and force the divergence where the value observed after a delete in-block and the value the JMT commits stop being consistent; the adjacent symbols in the same file that carry the value are `ProverStorage`, `ProverStateUpdate`, `committable_latest_version`, `uncommittable_with_version`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: deletes are visible identically natively and in-circuit
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: write/delete/read the same key and diff roots
