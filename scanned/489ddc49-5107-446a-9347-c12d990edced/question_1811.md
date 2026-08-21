# Q1811: ChainBaseManager: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getBlockById` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker calls a count/size path backed by ChainBaseManager.getBlockById that iterates the whole store per request — to break the invariant that ChainBaseManager.getBlockById answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getBlockById`
- Entrypoint: query backed by ChainBaseManager.getBlockById
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getBlockById` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ChainBaseManager.getBlockById that iterates the whole store per request
- Invariant to test: ChainBaseManager.getBlockById answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ChainBaseManager.getBlockById cost vs store size
