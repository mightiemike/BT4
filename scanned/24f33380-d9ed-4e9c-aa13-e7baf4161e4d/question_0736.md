# Q736: ChainBaseManager: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getBlockByNum` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker calls a count/size path backed by ChainBaseManager.getBlockByNum that iterates the whole store per request — to break the invariant that ChainBaseManager.getBlockByNum answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getBlockByNum`
- Entrypoint: query backed by ChainBaseManager.getBlockByNum
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getBlockByNum` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ChainBaseManager.getBlockByNum that iterates the whole store per request
- Invariant to test: ChainBaseManager.getBlockByNum answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ChainBaseManager.getBlockByNum cost vs store size
