# Q882: TronStoreWithRevoking: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.closeJniIterator` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker calls a count/size path backed by TronStoreWithRevoking.closeJniIterator that iterates the whole store per request — to break the invariant that TronStoreWithRevoking.closeJniIterator answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.closeJniIterator`
- Entrypoint: query backed by TronStoreWithRevoking.closeJniIterator
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.closeJniIterator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by TronStoreWithRevoking.closeJniIterator that iterates the whole store per request
- Invariant to test: TronStoreWithRevoking.closeJniIterator answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring TronStoreWithRevoking.closeJniIterator cost vs store size
