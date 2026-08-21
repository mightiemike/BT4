# Q3405: StoreIterator: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `StoreIterator.getValue` in `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` — where the attacker seeds keys so a query iterating StoreIterator.getValue performs an unbounded prefix scan on each request — to break the invariant that iteration in StoreIterator.getValue is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/StoreIterator.java` -> `StoreIterator.getValue`
- Entrypoint: query backed by StoreIterator.getValue after seeding keys
- Attacker controls: request/transaction/contract inputs to `StoreIterator.getValue` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating StoreIterator.getValue performs an unbounded prefix scan on each request
- Invariant to test: iteration in StoreIterator.getValue is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring StoreIterator.getValue scan growth
