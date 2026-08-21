# Q437: ChainBaseManager: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getGenesisBlockId` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker calls a count/size path backed by ChainBaseManager.getGenesisBlockId that iterates the whole store per request — to break the invariant that ChainBaseManager.getGenesisBlockId answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getGenesisBlockId`
- Entrypoint: query backed by ChainBaseManager.getGenesisBlockId
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getGenesisBlockId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ChainBaseManager.getGenesisBlockId that iterates the whole store per request
- Invariant to test: ChainBaseManager.getGenesisBlockId answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ChainBaseManager.getGenesisBlockId cost vs store size
