# Q130: ContractStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ContractStore.findContractByHash` in `chainbase/src/main/java/org/tron/core/store/ContractStore.java` — where the attacker calls a count/size path backed by ContractStore.findContractByHash that iterates the whole store per request — to break the invariant that ContractStore.findContractByHash answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/ContractStore.java` -> `ContractStore.findContractByHash`
- Entrypoint: query backed by ContractStore.findContractByHash
- Attacker controls: request/transaction/contract inputs to `ContractStore.findContractByHash` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ContractStore.findContractByHash that iterates the whole store per request
- Invariant to test: ContractStore.findContractByHash answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ContractStore.findContractByHash cost vs store size
