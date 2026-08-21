# Q1375: StoreIterator: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `StoreIterator.getKey` in `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` — where the attacker calls a count/size path backed by StoreIterator.getKey that iterates the whole store per request — to break the invariant that StoreIterator.getKey answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` -> `StoreIterator.getKey`
- Entrypoint: query backed by StoreIterator.getKey
- Attacker controls: request/transaction/contract inputs to `StoreIterator.getKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by StoreIterator.getKey that iterates the whole store per request
- Invariant to test: StoreIterator.getKey answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring StoreIterator.getKey cost vs store size
