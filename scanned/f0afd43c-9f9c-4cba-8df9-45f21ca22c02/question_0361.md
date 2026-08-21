# Q361: StoreIterator: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `StoreIterator.getKey` in `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` — where the attacker triggers StoreIterator.getKey paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in StoreIterator.getKey is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` -> `StoreIterator.getKey`
- Entrypoint: repeated queries via StoreIterator.getKey
- Attacker controls: request/transaction/contract inputs to `StoreIterator.getKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers StoreIterator.getKey paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in StoreIterator.getKey is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress StoreIterator.getKey and watch handle/heap growth
