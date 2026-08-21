# Q70: RockStoreIterator: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `RockStoreIterator.getValue` in `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` — where the attacker triggers RockStoreIterator.getValue paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in RockStoreIterator.getValue is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` -> `RockStoreIterator.getValue`
- Entrypoint: repeated queries via RockStoreIterator.getValue
- Attacker controls: request/transaction/contract inputs to `RockStoreIterator.getValue` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers RockStoreIterator.getValue paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in RockStoreIterator.getValue is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress RockStoreIterator.getValue and watch handle/heap growth
