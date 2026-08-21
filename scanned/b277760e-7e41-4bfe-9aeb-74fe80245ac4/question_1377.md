# Q1377: StoreIterator: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `StoreIterator.getValue` in `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` — where the attacker calls a count/size path backed by StoreIterator.getValue that iterates the whole store per request — to break the invariant that StoreIterator.getValue answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` -> `StoreIterator.getValue`
- Entrypoint: query backed by StoreIterator.getValue
- Attacker controls: request/transaction/contract inputs to `StoreIterator.getValue` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by StoreIterator.getValue that iterates the whole store per request
- Invariant to test: StoreIterator.getValue answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring StoreIterator.getValue cost vs store size
