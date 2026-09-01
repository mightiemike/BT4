# Q0619: cache pruning height handling via `mark_all_as_read` (cache.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key in one block, controlling which JMT keys are read and written, drive `mark_all_as_read` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/cache.rs` so that the cache prune heights the circuit applies and the heights the native node applied stop being the same, breaking the invariant that pruning never changes the computed root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/cache.rs` -> `mark_all_as_read`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key in one block
- Attacker controls: which JMT keys are read and written
- Exploit idea: cache pruning height handling - reach `mark_all_as_read` from that entrypoint and force the divergence where the cache prune heights the circuit applies and the heights the native node applied stop being the same; the adjacent symbols in the same file that carry the value are `CacheKey`, `CacheValue`, `Access`, `ValueExists`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: pruning never changes the computed root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: prune at adversarial heights and diff roots
