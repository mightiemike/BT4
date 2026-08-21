# Q3607: TronDatabase: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `TronDatabase.getFromRoot` in `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` — where the attacker seeds keys so a query iterating TronDatabase.getFromRoot performs an unbounded prefix scan on each request — to break the invariant that iteration in TronDatabase.getFromRoot is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronDatabase.java` -> `TronDatabase.getFromRoot`
- Entrypoint: query backed by TronDatabase.getFromRoot after seeding keys
- Attacker controls: request/transaction/contract inputs to `TronDatabase.getFromRoot` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating TronDatabase.getFromRoot performs an unbounded prefix scan on each request
- Invariant to test: iteration in TronDatabase.getFromRoot is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring TronDatabase.getFromRoot scan growth
