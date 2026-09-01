# Q0192: cache coherence with storage via `put` (cache.rs)

## Question
Can an unprivileged attacker who drives a node's caches with queries for state it is concurrently updating, controlling the query pattern against the cache, drive `put` in `crates/common/src/cache.rs` so that the value served from cache and the value in storage stop being the same, breaking the invariant that caches never diverge from storage?

## Target
- File/function: `crates/common/src/cache.rs` -> `put`
- Entrypoint: unprivileged party drives a node's caches with queries for state it is concurrently updating
- Attacker controls: the query pattern against the cache
- Exploit idea: cache coherence with storage - reach `put` from that entrypoint and force the divergence where the value served from cache and the value in storage stop being the same; the adjacent symbols in the same file that carry the value are `L1BlockCache`, `get`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: caches never diverge from storage
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: invalidate under load and diff
