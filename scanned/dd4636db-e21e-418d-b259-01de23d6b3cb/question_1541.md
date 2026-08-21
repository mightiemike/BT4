# Q1541: TronDatabase: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.has` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker calls a count/size path backed by TronDatabase.has that iterates the whole store per request — to break the invariant that TronDatabase.has answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.has`
- Entrypoint: query backed by TronDatabase.has
- Attacker controls: request/transaction/contract inputs to `TronDatabase.has` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by TronDatabase.has that iterates the whole store per request
- Invariant to test: TronDatabase.has answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring TronDatabase.has cost vs store size
