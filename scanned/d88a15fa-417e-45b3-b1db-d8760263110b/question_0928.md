# Q928: TronDatabase: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.iterator` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker calls a count/size path backed by TronDatabase.iterator that iterates the whole store per request — to break the invariant that TronDatabase.iterator answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.iterator`
- Entrypoint: query backed by TronDatabase.iterator
- Attacker controls: request/transaction/contract inputs to `TronDatabase.iterator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by TronDatabase.iterator that iterates the whole store per request
- Invariant to test: TronDatabase.iterator answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring TronDatabase.iterator cost vs store size
