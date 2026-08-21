# Q3844: DynamicPropertiesStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMaintenanceTimeInterval` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker seeds keys so a query iterating DynamicPropertiesStore.getMaintenanceTimeInterval performs an unbounded prefix scan on each request — to break the invariant that iteration in DynamicPropertiesStore.getMaintenanceTimeInterval is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMaintenanceTimeInterval`
- Entrypoint: query backed by DynamicPropertiesStore.getMaintenanceTimeInterval after seeding keys
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMaintenanceTimeInterval` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating DynamicPropertiesStore.getMaintenanceTimeInterval performs an unbounded prefix scan on each request
- Invariant to test: iteration in DynamicPropertiesStore.getMaintenanceTimeInterval is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring DynamicPropertiesStore.getMaintenanceTimeInterval scan growth
