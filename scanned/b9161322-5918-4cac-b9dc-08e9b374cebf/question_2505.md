# Q2505: ChainBaseManager: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getHeadBlockId` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker calls a count/size path backed by ChainBaseManager.getHeadBlockId that iterates the whole store per request — to break the invariant that ChainBaseManager.getHeadBlockId answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getHeadBlockId`
- Entrypoint: query backed by ChainBaseManager.getHeadBlockId
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getHeadBlockId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ChainBaseManager.getHeadBlockId that iterates the whole store per request
- Invariant to test: ChainBaseManager.getHeadBlockId answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ChainBaseManager.getHeadBlockId cost vs store size
