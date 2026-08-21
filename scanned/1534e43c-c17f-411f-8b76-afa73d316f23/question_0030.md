# Q30: RecentTransactionStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `RecentTransactionStore.<primary method>` in `chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java` — where the attacker calls a count/size path backed by RecentTransactionStore.<primary method> that iterates the whole store per request — to break the invariant that RecentTransactionStore.<primary method> answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/RecentTransactionStore.java` -> `RecentTransactionStore.<primary method>`
- Entrypoint: query backed by RecentTransactionStore.<primary method>
- Attacker controls: request/transaction/contract inputs to `RecentTransactionStore.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by RecentTransactionStore.<primary method> that iterates the whole store per request
- Invariant to test: RecentTransactionStore.<primary method> answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring RecentTransactionStore.<primary method> cost vs store size
