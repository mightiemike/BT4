# Q3718: delete-then-read semantics via `compute_state_update` (zk_storage.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key in one block, controlling the size and shape of the state diff, drive `compute_state_update` in `crates/sovereign-sdk/module-system/sov-state/src/zk_storage.rs` so that the value observed after a delete in-block and the value the JMT commits stop being consistent, breaking the invariant that deletes are visible identically natively and in-circuit?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/zk_storage.rs` -> `compute_state_update`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key in one block
- Attacker controls: the size and shape of the state diff
- Exploit idea: delete-then-read semantics - reach `compute_state_update` from that entrypoint and force the divergence where the value observed after a delete in-block and the value the JMT commits stop being consistent; the adjacent symbols in the same file that carry the value are `ZkStorage`, `get`, `get_and_prove`, `get_offchain`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: deletes are visible identically natively and in-circuit
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: write/delete/read the same key and diff roots
