# Q2168: TronDatabase: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.getDbSource` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker seeds keys so a query iterating TronDatabase.getDbSource performs an unbounded prefix scan on each request — to break the invariant that iteration in TronDatabase.getDbSource is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.getDbSource`
- Entrypoint: query backed by TronDatabase.getDbSource after seeding keys
- Attacker controls: request/transaction/contract inputs to `TronDatabase.getDbSource` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating TronDatabase.getDbSource performs an unbounded prefix scan on each request
- Invariant to test: iteration in TronDatabase.getDbSource is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring TronDatabase.getDbSource scan growth
