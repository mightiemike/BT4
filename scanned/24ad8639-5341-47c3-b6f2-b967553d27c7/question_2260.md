# Q2260: ChainBaseManager: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getHeadBlockNum` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker calls a count/size path backed by ChainBaseManager.getHeadBlockNum that iterates the whole store per request — to break the invariant that ChainBaseManager.getHeadBlockNum answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getHeadBlockNum`
- Entrypoint: query backed by ChainBaseManager.getHeadBlockNum
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getHeadBlockNum` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ChainBaseManager.getHeadBlockNum that iterates the whole store per request
- Invariant to test: ChainBaseManager.getHeadBlockNum answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ChainBaseManager.getHeadBlockNum cost vs store size
