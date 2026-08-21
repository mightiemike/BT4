# Q80: TronStoreWithRevoking: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.iterator` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker seeds keys so a query iterating TronStoreWithRevoking.iterator performs an unbounded prefix scan on each request — to break the invariant that iteration in TronStoreWithRevoking.iterator is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.iterator`
- Entrypoint: query backed by TronStoreWithRevoking.iterator after seeding keys
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.iterator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating TronStoreWithRevoking.iterator performs an unbounded prefix scan on each request
- Invariant to test: iteration in TronStoreWithRevoking.iterator is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring TronStoreWithRevoking.iterator scan growth
