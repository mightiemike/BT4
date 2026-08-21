# Q2400: TronDatabase: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.getUnchecked` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker calls a count/size path backed by TronDatabase.getUnchecked that iterates the whole store per request — to break the invariant that TronDatabase.getUnchecked answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.getUnchecked`
- Entrypoint: query backed by TronDatabase.getUnchecked
- Attacker controls: request/transaction/contract inputs to `TronDatabase.getUnchecked` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by TronDatabase.getUnchecked that iterates the whole store per request
- Invariant to test: TronDatabase.getUnchecked answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring TronDatabase.getUnchecked cost vs store size
