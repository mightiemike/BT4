# Q3712: AccountIdIndexStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `AccountIdIndexStore.put` in `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` — where the attacker calls a count/size path backed by AccountIdIndexStore.put that iterates the whole store per request — to break the invariant that AccountIdIndexStore.put answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` -> `AccountIdIndexStore.put`
- Entrypoint: query backed by AccountIdIndexStore.put
- Attacker controls: request/transaction/contract inputs to `AccountIdIndexStore.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by AccountIdIndexStore.put that iterates the whole store per request
- Invariant to test: AccountIdIndexStore.put answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring AccountIdIndexStore.put cost vs store size
