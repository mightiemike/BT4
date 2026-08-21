# Q349: TronDatabase: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.getDbSource` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker triggers TronDatabase.getDbSource paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in TronDatabase.getDbSource is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.getDbSource`
- Entrypoint: repeated queries via TronDatabase.getDbSource
- Attacker controls: request/transaction/contract inputs to `TronDatabase.getDbSource` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers TronDatabase.getDbSource paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in TronDatabase.getDbSource is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress TronDatabase.getDbSource and watch handle/heap growth
