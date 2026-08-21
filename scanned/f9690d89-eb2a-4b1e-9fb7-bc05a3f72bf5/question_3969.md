# Q3969: CodeStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `CodeStore.getTotalCodes` in `chainbase/src/main/java/org/tron/core/store/CodeStore.java` — where the attacker seeds keys so a query iterating CodeStore.getTotalCodes performs an unbounded prefix scan on each request — to break the invariant that iteration in CodeStore.getTotalCodes is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/CodeStore.java` -> `CodeStore.getTotalCodes`
- Entrypoint: query backed by CodeStore.getTotalCodes after seeding keys
- Attacker controls: request/transaction/contract inputs to `CodeStore.getTotalCodes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating CodeStore.getTotalCodes performs an unbounded prefix scan on each request
- Invariant to test: iteration in CodeStore.getTotalCodes is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring CodeStore.getTotalCodes scan growth
