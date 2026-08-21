# Q447: ContractStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ContractStore.put` in `chainbase/src/main/java/org/tron/core/store/ContractStore.java` — where the attacker calls a count/size path backed by ContractStore.put that iterates the whole store per request — to break the invariant that ContractStore.put answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/ContractStore.java` -> `ContractStore.put`
- Entrypoint: query backed by ContractStore.put
- Attacker controls: request/transaction/contract inputs to `ContractStore.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ContractStore.put that iterates the whole store per request
- Invariant to test: ContractStore.put answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ContractStore.put cost vs store size
