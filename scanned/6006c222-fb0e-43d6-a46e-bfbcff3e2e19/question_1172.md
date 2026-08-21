# Q1172: AccountIdIndexStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `AccountIdIndexStore.getLowerCaseAccountId` in `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` — where the attacker seeds keys so a query iterating AccountIdIndexStore.getLowerCaseAccountId performs an unbounded prefix scan on each request — to break the invariant that iteration in AccountIdIndexStore.getLowerCaseAccountId is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java` -> `AccountIdIndexStore.getLowerCaseAccountId`
- Entrypoint: query backed by AccountIdIndexStore.getLowerCaseAccountId after seeding keys
- Attacker controls: request/transaction/contract inputs to `AccountIdIndexStore.getLowerCaseAccountId` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating AccountIdIndexStore.getLowerCaseAccountId performs an unbounded prefix scan on each request
- Invariant to test: iteration in AccountIdIndexStore.getLowerCaseAccountId is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring AccountIdIndexStore.getLowerCaseAccountId scan growth
