# Q369: RockStoreIterator: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `RockStoreIterator.getValue` in `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` — where the attacker seeds keys so a query iterating RockStoreIterator.getValue performs an unbounded prefix scan on each request — to break the invariant that iteration in RockStoreIterator.getValue is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/common/iterator/RockStoreIterator.java` -> `RockStoreIterator.getValue`
- Entrypoint: query backed by RockStoreIterator.getValue after seeding keys
- Attacker controls: request/transaction/contract inputs to `RockStoreIterator.getValue` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating RockStoreIterator.getValue performs an unbounded prefix scan on each request
- Invariant to test: iteration in RockStoreIterator.getValue is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring RockStoreIterator.getValue scan growth
