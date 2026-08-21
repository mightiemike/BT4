# Q2428: ContractStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ContractStore.getTotalContracts` in `chainbase/src/main/java/org/tron/core/store/ContractStore.java` — where the attacker calls a count/size path backed by ContractStore.getTotalContracts that iterates the whole store per request — to break the invariant that ContractStore.getTotalContracts answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/ContractStore.java` -> `ContractStore.getTotalContracts`
- Entrypoint: query backed by ContractStore.getTotalContracts
- Attacker controls: request/transaction/contract inputs to `ContractStore.getTotalContracts` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ContractStore.getTotalContracts that iterates the whole store per request
- Invariant to test: ContractStore.getTotalContracts answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ContractStore.getTotalContracts cost vs store size
