# Q3618: TronDatabase: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.getUnchecked` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker triggers TronDatabase.getUnchecked paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in TronDatabase.getUnchecked is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.getUnchecked`
- Entrypoint: repeated queries via TronDatabase.getUnchecked
- Attacker controls: request/transaction/contract inputs to `TronDatabase.getUnchecked` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers TronDatabase.getUnchecked paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in TronDatabase.getUnchecked is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress TronDatabase.getUnchecked and watch handle/heap growth
