# Q2154: RockStoreIterator: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `RockStoreIterator.getKey` in `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` — where the attacker calls a count/size path backed by RockStoreIterator.getKey that iterates the whole store per request — to break the invariant that RockStoreIterator.getKey answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` -> `RockStoreIterator.getKey`
- Entrypoint: query backed by RockStoreIterator.getKey
- Attacker controls: request/transaction/contract inputs to `RockStoreIterator.getKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by RockStoreIterator.getKey that iterates the whole store per request
- Invariant to test: RockStoreIterator.getKey answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring RockStoreIterator.getKey cost vs store size
