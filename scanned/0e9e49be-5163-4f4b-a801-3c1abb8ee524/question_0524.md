# Q524: CodeStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `CodeStore.findCodeByHash` in `chainbase/src/main/java/org/tron/core/store/CodeStore.java` — where the attacker calls a count/size path backed by CodeStore.findCodeByHash that iterates the whole store per request — to break the invariant that CodeStore.findCodeByHash answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/CodeStore.java` -> `CodeStore.findCodeByHash`
- Entrypoint: query backed by CodeStore.findCodeByHash
- Attacker controls: request/transaction/contract inputs to `CodeStore.findCodeByHash` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by CodeStore.findCodeByHash that iterates the whole store per request
- Invariant to test: CodeStore.findCodeByHash answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring CodeStore.findCodeByHash cost vs store size
