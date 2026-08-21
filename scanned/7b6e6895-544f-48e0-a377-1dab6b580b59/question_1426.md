# Q1426: TronDatabase: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.prefixQuery` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker triggers TronDatabase.prefixQuery paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in TronDatabase.prefixQuery is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.prefixQuery`
- Entrypoint: repeated queries via TronDatabase.prefixQuery
- Attacker controls: request/transaction/contract inputs to `TronDatabase.prefixQuery` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers TronDatabase.prefixQuery paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in TronDatabase.prefixQuery is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress TronDatabase.prefixQuery and watch handle/heap growth
