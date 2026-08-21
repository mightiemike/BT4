# Q784: TronDatabase: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.prefixQuery` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker calls a count/size path backed by TronDatabase.prefixQuery that iterates the whole store per request — to break the invariant that TronDatabase.prefixQuery answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.prefixQuery`
- Entrypoint: query backed by TronDatabase.prefixQuery
- Attacker controls: request/transaction/contract inputs to `TronDatabase.prefixQuery` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by TronDatabase.prefixQuery that iterates the whole store per request
- Invariant to test: TronDatabase.prefixQuery answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring TronDatabase.prefixQuery cost vs store size
