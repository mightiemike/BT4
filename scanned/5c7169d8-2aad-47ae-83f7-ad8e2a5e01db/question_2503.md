# Q2503: RecentTransactionStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `RecentTransactionStore.<primary method>` in `chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java` — where the attacker seeds keys so a query iterating RecentTransactionStore.<primary method> performs an unbounded prefix scan on each request — to break the invariant that iteration in RecentTransactionStore.<primary method> is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java` -> `RecentTransactionStore.<primary method>`
- Entrypoint: query backed by RecentTransactionStore.<primary method> after seeding keys
- Attacker controls: request/transaction/contract inputs to `RecentTransactionStore.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating RecentTransactionStore.<primary method> performs an unbounded prefix scan on each request
- Invariant to test: iteration in RecentTransactionStore.<primary method> is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring RecentTransactionStore.<primary method> scan growth
