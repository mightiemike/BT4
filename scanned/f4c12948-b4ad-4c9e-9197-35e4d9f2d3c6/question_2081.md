# Q2081: TronDatabase: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.getName` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker triggers TronDatabase.getName paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in TronDatabase.getName is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.getName`
- Entrypoint: repeated queries via TronDatabase.getName
- Attacker controls: request/transaction/contract inputs to `TronDatabase.getName` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers TronDatabase.getName paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in TronDatabase.getName is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress TronDatabase.getName and watch handle/heap growth
