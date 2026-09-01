# Q0522: cache coherence with storage via `from_env` (risc0.rs)

## Question
Can an unprivileged attacker who drives a node's caches with queries for state it is concurrently updating, controlling the block tags requested, drive `from_env` in `crates/common/src/config/risc0.rs` so that the value served from cache and the value in storage stop being the same, breaking the invariant that caches never diverge from storage?

## Target
- File/function: `crates/common/src/config/risc0.rs` -> `from_env`
- Entrypoint: unprivileged party drives a node's caches with queries for state it is concurrently updating
- Attacker controls: the block tags requested
- Exploit idea: cache coherence with storage - reach `from_env` from that entrypoint and force the divergence where the value served from cache and the value in storage stop being the same; the adjacent symbols in the same file that carry the value are `BoundlessS3StorageConfig`, `BoundlessPinataStorageConfig`, `BoundlessStorageConfig`, `BoundlessProverConfig`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: caches never diverge from storage
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: invalidate under load and diff
