# Q1971: ChainBaseManager: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getHeadSlot` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker calls a count/size path backed by ChainBaseManager.getHeadSlot that iterates the whole store per request — to break the invariant that ChainBaseManager.getHeadSlot answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getHeadSlot`
- Entrypoint: query backed by ChainBaseManager.getHeadSlot
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getHeadSlot` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ChainBaseManager.getHeadSlot that iterates the whole store per request
- Invariant to test: ChainBaseManager.getHeadSlot answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ChainBaseManager.getHeadSlot cost vs store size
