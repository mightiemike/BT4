# Q2822: DBIterator: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `DBIterator.<primary method>` in `chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java` — where the attacker calls a count/size path backed by DBIterator.<primary method> that iterates the whole store per request — to break the invariant that DBIterator.<primary method> answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java` -> `DBIterator.<primary method>`
- Entrypoint: query backed by DBIterator.<primary method>
- Attacker controls: request/transaction/contract inputs to `DBIterator.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by DBIterator.<primary method> that iterates the whole store per request
- Invariant to test: DBIterator.<primary method> answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring DBIterator.<primary method> cost vs store size
