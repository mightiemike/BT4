# Q2026: DynamicPropertiesStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMaxFrozenTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker seeds keys so a query iterating DynamicPropertiesStore.getMaxFrozenTime performs an unbounded prefix scan on each request — to break the invariant that iteration in DynamicPropertiesStore.getMaxFrozenTime is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMaxFrozenTime`
- Entrypoint: query backed by DynamicPropertiesStore.getMaxFrozenTime after seeding keys
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMaxFrozenTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating DynamicPropertiesStore.getMaxFrozenTime performs an unbounded prefix scan on each request
- Invariant to test: iteration in DynamicPropertiesStore.getMaxFrozenTime is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring DynamicPropertiesStore.getMaxFrozenTime scan growth
