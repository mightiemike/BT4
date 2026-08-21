# Q2864: AccountStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `AccountStore.put` in `chainbase/src/main/java/org/tron/core/store/AccountStore.java` — where the attacker calls a count/size path backed by AccountStore.put that iterates the whole store per request — to break the invariant that AccountStore.put answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountStore.java` -> `AccountStore.put`
- Entrypoint: query backed by AccountStore.put
- Attacker controls: request/transaction/contract inputs to `AccountStore.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by AccountStore.put that iterates the whole store per request
- Invariant to test: AccountStore.put answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring AccountStore.put cost vs store size
