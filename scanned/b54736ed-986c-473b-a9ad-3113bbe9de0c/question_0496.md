# Q496: RockStoreIterator: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `RockStoreIterator.getValue` in `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` — where the attacker calls a count/size path backed by RockStoreIterator.getValue that iterates the whole store per request — to break the invariant that RockStoreIterator.getValue answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` -> `RockStoreIterator.getValue`
- Entrypoint: query backed by RockStoreIterator.getValue
- Attacker controls: request/transaction/contract inputs to `RockStoreIterator.getValue` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by RockStoreIterator.getValue that iterates the whole store per request
- Invariant to test: RockStoreIterator.getValue answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring RockStoreIterator.getValue cost vs store size
