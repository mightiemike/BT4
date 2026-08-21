# Q1225: AccountStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `AccountStore.getBlackhole` in `chainbase/src/main/java/org/tron/core/store/AccountStore.java` — where the attacker calls a count/size path backed by AccountStore.getBlackhole that iterates the whole store per request — to break the invariant that AccountStore.getBlackhole answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountStore.java` -> `AccountStore.getBlackhole`
- Entrypoint: query backed by AccountStore.getBlackhole
- Attacker controls: request/transaction/contract inputs to `AccountStore.getBlackhole` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by AccountStore.getBlackhole that iterates the whole store per request
- Invariant to test: AccountStore.getBlackhole answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring AccountStore.getBlackhole cost vs store size
