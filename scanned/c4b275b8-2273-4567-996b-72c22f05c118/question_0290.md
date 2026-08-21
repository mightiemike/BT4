# Q290: ChainBaseManager: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `ChainBaseManager.getKhaosDbHead` in `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` — where the attacker calls a count/size path backed by ChainBaseManager.getKhaosDbHead that iterates the whole store per request — to break the invariant that ChainBaseManager.getKhaosDbHead answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/ChainBaseManager.java` -> `ChainBaseManager.getKhaosDbHead`
- Entrypoint: query backed by ChainBaseManager.getKhaosDbHead
- Attacker controls: request/transaction/contract inputs to `ChainBaseManager.getKhaosDbHead` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by ChainBaseManager.getKhaosDbHead that iterates the whole store per request
- Invariant to test: ChainBaseManager.getKhaosDbHead answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring ChainBaseManager.getKhaosDbHead cost vs store size
