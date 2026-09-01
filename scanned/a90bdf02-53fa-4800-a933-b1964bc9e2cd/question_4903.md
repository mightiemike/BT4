# Q4903: delete-then-read semantics via `iter_writes` (cache.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key in one block, controlling intra-block ordering of its own transactions, drive `iter_writes` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/cache.rs` so that the value observed after a delete in-block and the value the JMT commits stop being consistent, breaking the invariant that deletes are visible identically natively and in-circuit?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/cache.rs` -> `iter_writes`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key in one block
- Attacker controls: intra-block ordering of its own transactions
- Exploit idea: delete-then-read semantics - reach `iter_writes` from that entrypoint and force the divergence where the value observed after a delete in-block and the value the JMT commits stop being consistent; the adjacent symbols in the same file that carry the value are `CacheKey`, `CacheValue`, `Access`, `ValueExists`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: deletes are visible identically natively and in-circuit
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: write/delete/read the same key and diff roots
