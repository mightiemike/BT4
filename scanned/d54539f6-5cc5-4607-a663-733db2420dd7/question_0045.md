# Q45: TronStoreWithRevoking: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.getName` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker triggers TronStoreWithRevoking.getName paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in TronStoreWithRevoking.getName is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.getName`
- Entrypoint: repeated queries via TronStoreWithRevoking.getName
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.getName` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers TronStoreWithRevoking.getName paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in TronStoreWithRevoking.getName is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress TronStoreWithRevoking.getName and watch handle/heap growth
