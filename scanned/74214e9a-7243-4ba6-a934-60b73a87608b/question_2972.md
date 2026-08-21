# Q2972: DynamicPropertiesStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getWitnessAllowanceFrozenTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker seeds keys so a query iterating DynamicPropertiesStore.getWitnessAllowanceFrozenTime performs an unbounded prefix scan on each request — to break the invariant that iteration in DynamicPropertiesStore.getWitnessAllowanceFrozenTime is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getWitnessAllowanceFrozenTime`
- Entrypoint: query backed by DynamicPropertiesStore.getWitnessAllowanceFrozenTime after seeding keys
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getWitnessAllowanceFrozenTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating DynamicPropertiesStore.getWitnessAllowanceFrozenTime performs an unbounded prefix scan on each request
- Invariant to test: iteration in DynamicPropertiesStore.getWitnessAllowanceFrozenTime is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring DynamicPropertiesStore.getWitnessAllowanceFrozenTime scan growth
