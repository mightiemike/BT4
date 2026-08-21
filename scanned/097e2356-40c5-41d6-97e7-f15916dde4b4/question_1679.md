# Q1679: ChainBaseManager: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.hasBlocks` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker calls a count/size path backed by ChainBaseManager.hasBlocks that iterates the whole store per request — to break the invariant that ChainBaseManager.hasBlocks answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.hasBlocks`
- Entrypoint: query backed by ChainBaseManager.hasBlocks
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.hasBlocks` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ChainBaseManager.hasBlocks that iterates the whole store per request
- Invariant to test: ChainBaseManager.hasBlocks answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ChainBaseManager.hasBlocks cost vs store size
