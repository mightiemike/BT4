# Q255: DBIterator: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `DBIterator.<primary method>` in `chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java` — where the attacker seeds keys so a query iterating DBIterator.<primary method> performs an unbounded prefix scan on each request — to break the invariant that iteration in DBIterator.<primary method> is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/DBIterator.java` -> `DBIterator.<primary method>`
- Entrypoint: query backed by DBIterator.<primary method> after seeding keys
- Attacker controls: request/transaction/contract inputs to `DBIterator.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating DBIterator.<primary method> performs an unbounded prefix scan on each request
- Invariant to test: iteration in DBIterator.<primary method> is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring DBIterator.<primary method> scan growth
