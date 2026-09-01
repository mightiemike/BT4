# Q1419: cache pruning height handling via `estimated_cache_size` (cache.rs)

## Question
Can an unprivileged attacker who sends an L2 transaction whose execution touches a JMT slot no other transaction touches, controlling intra-block ordering of its own transactions, drive `estimated_cache_size` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/cache.rs` so that the cache prune heights the circuit applies and the heights the native node applied stop being the same, breaking the invariant that pruning never changes the computed root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/cache.rs` -> `estimated_cache_size`
- Entrypoint: unprivileged party sends an L2 transaction whose execution touches a JMT slot no other transaction touches
- Attacker controls: intra-block ordering of its own transactions
- Exploit idea: cache pruning height handling - reach `estimated_cache_size` from that entrypoint and force the divergence where the cache prune heights the circuit applies and the heights the native node applied stop being the same; the adjacent symbols in the same file that carry the value are `CacheKey`, `CacheValue`, `Access`, `ValueExists`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: pruning never changes the computed root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: prune at adversarial heights and diff roots
