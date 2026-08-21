# Q2068: StoreIterator: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `StoreIterator.hasNext` in `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` — where the attacker calls a count/size path backed by StoreIterator.hasNext that iterates the whole store per request — to break the invariant that StoreIterator.hasNext answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` -> `StoreIterator.hasNext`
- Entrypoint: query backed by StoreIterator.hasNext
- Attacker controls: request/transaction/contract inputs to `StoreIterator.hasNext` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by StoreIterator.hasNext that iterates the whole store per request
- Invariant to test: StoreIterator.hasNext answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring StoreIterator.hasNext cost vs store size
