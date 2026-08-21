# Q3338: TronStoreWithRevoking: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.iterator` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker triggers TronStoreWithRevoking.iterator paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in TronStoreWithRevoking.iterator is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.iterator`
- Entrypoint: repeated queries via TronStoreWithRevoking.iterator
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.iterator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers TronStoreWithRevoking.iterator paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in TronStoreWithRevoking.iterator is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress TronStoreWithRevoking.iterator and watch handle/heap growth
