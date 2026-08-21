# Q3409: DynamicPropertiesStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMinFrozenSupplyTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker seeds keys so a query iterating DynamicPropertiesStore.getMinFrozenSupplyTime performs an unbounded prefix scan on each request — to break the invariant that iteration in DynamicPropertiesStore.getMinFrozenSupplyTime is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMinFrozenSupplyTime`
- Entrypoint: query backed by DynamicPropertiesStore.getMinFrozenSupplyTime after seeding keys
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMinFrozenSupplyTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating DynamicPropertiesStore.getMinFrozenSupplyTime performs an unbounded prefix scan on each request
- Invariant to test: iteration in DynamicPropertiesStore.getMinFrozenSupplyTime is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring DynamicPropertiesStore.getMinFrozenSupplyTime scan growth
