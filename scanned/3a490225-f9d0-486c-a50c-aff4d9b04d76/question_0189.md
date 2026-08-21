# Q189: RecentTransactionStore: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `RecentTransactionStore.<primary method>` in `chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java` — where the attacker triggers RecentTransactionStore.<primary method> paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in RecentTransactionStore.<primary method> is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java` -> `RecentTransactionStore.<primary method>`
- Entrypoint: repeated queries via RecentTransactionStore.<primary method>
- Attacker controls: request/transaction/contract inputs to `RecentTransactionStore.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers RecentTransactionStore.<primary method> paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in RecentTransactionStore.<primary method> is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress RecentTransactionStore.<primary method> and watch handle/heap growth
