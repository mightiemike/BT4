# Q3090: CodeStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `CodeStore.getTotalCodes` in `chainbase/src/main/java/org/tron/core/store/CodeStore.java` — where the attacker calls a count/size path backed by CodeStore.getTotalCodes that iterates the whole store per request — to break the invariant that CodeStore.getTotalCodes answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/CodeStore.java` -> `CodeStore.getTotalCodes`
- Entrypoint: query backed by CodeStore.getTotalCodes
- Attacker controls: request/transaction/contract inputs to `CodeStore.getTotalCodes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by CodeStore.getTotalCodes that iterates the whole store per request
- Invariant to test: CodeStore.getTotalCodes answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring CodeStore.getTotalCodes cost vs store size
