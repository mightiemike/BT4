# Q1306: TronStoreWithRevoking: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.getFromRoot` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker calls a count/size path backed by TronStoreWithRevoking.getFromRoot that iterates the whole store per request — to break the invariant that TronStoreWithRevoking.getFromRoot answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.getFromRoot`
- Entrypoint: query backed by TronStoreWithRevoking.getFromRoot
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.getFromRoot` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by TronStoreWithRevoking.getFromRoot that iterates the whole store per request
- Invariant to test: TronStoreWithRevoking.getFromRoot answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring TronStoreWithRevoking.getFromRoot cost vs store size
