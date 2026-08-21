# Q1693: TronDatabase: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.getName` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker calls a count/size path backed by TronDatabase.getName that iterates the whole store per request — to break the invariant that TronDatabase.getName answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.getName`
- Entrypoint: query backed by TronDatabase.getName
- Attacker controls: request/transaction/contract inputs to `TronDatabase.getName` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by TronDatabase.getName that iterates the whole store per request
- Invariant to test: TronDatabase.getName answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring TronDatabase.getName cost vs store size
