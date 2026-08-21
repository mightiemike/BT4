# Q3293: TronStoreWithRevoking: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.getName` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker calls a count/size path backed by TronStoreWithRevoking.getName that iterates the whole store per request — to break the invariant that TronStoreWithRevoking.getName answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.getName`
- Entrypoint: query backed by TronStoreWithRevoking.getName
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.getName` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by TronStoreWithRevoking.getName that iterates the whole store per request
- Invariant to test: TronStoreWithRevoking.getName answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring TronStoreWithRevoking.getName cost vs store size
