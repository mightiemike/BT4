# Q2140: RockStoreIterator: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `RockStoreIterator.getKey` in `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` — where the attacker triggers RockStoreIterator.getKey paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in RockStoreIterator.getKey is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` -> `RockStoreIterator.getKey`
- Entrypoint: repeated queries via RockStoreIterator.getKey
- Attacker controls: request/transaction/contract inputs to `RockStoreIterator.getKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers RockStoreIterator.getKey paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in RockStoreIterator.getKey is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress RockStoreIterator.getKey and watch handle/heap growth
