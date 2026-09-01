# Q5638: delete-then-read semantics via `is_empty` (zk_storage.rs)

## Question
Can an unprivileged attacker who sends a transaction that reads state written earlier in the same block by another of its transactions, controlling intra-block ordering of its own transactions, drive `is_empty` in `crates/sovereign-sdk/module-system/sov-state/src/zk_storage.rs` so that the value observed after a delete in-block and the value the JMT commits stop being consistent, breaking the invariant that deletes are visible identically natively and in-circuit?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/zk_storage.rs` -> `is_empty`
- Entrypoint: unprivileged party sends a transaction that reads state written earlier in the same block by another of its transactions
- Attacker controls: intra-block ordering of its own transactions
- Exploit idea: delete-then-read semantics - reach `is_empty` from that entrypoint and force the divergence where the value observed after a delete in-block and the value the JMT commits stop being consistent; the adjacent symbols in the same file that carry the value are `ZkStorage`, `get`, `get_and_prove`, `get_offchain`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: deletes are visible identically natively and in-circuit
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: write/delete/read the same key and diff roots
