# Q3983: TronStoreWithRevoking: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.prefixQuery` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker calls a count/size path backed by TronStoreWithRevoking.prefixQuery that iterates the whole store per request — to break the invariant that TronStoreWithRevoking.prefixQuery answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.prefixQuery`
- Entrypoint: query backed by TronStoreWithRevoking.prefixQuery
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.prefixQuery` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by TronStoreWithRevoking.prefixQuery that iterates the whole store per request
- Invariant to test: TronStoreWithRevoking.prefixQuery answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring TronStoreWithRevoking.prefixQuery cost vs store size
