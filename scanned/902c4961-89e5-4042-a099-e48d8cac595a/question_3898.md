# Q3898: DynamicPropertiesStore: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMaxFrozenSupplyTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker crafts a key consumed by DynamicPropertiesStore.getMaxFrozenSupplyTime that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in DynamicPropertiesStore.getMaxFrozenSupplyTime are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMaxFrozenSupplyTime`
- Entrypoint: write via a path using DynamicPropertiesStore.getMaxFrozenSupplyTime
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMaxFrozenSupplyTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by DynamicPropertiesStore.getMaxFrozenSupplyTime that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in DynamicPropertiesStore.getMaxFrozenSupplyTime are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
