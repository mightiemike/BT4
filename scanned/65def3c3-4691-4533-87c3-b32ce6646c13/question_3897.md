# Q3897: AccountStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `AccountStore.getBlackhole` in `chainbase/src/main/java/org/tron/core/store/AccountStore.java` — where the attacker seeds keys so a query iterating AccountStore.getBlackhole performs an unbounded prefix scan on each request — to break the invariant that iteration in AccountStore.getBlackhole is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountStore.java` -> `AccountStore.getBlackhole`
- Entrypoint: query backed by AccountStore.getBlackhole after seeding keys
- Attacker controls: request/transaction/contract inputs to `AccountStore.getBlackhole` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating AccountStore.getBlackhole performs an unbounded prefix scan on each request
- Invariant to test: iteration in AccountStore.getBlackhole is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring AccountStore.getBlackhole scan growth
