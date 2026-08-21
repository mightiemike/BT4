# Q663: DynamicPropertiesStore: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getTokenUpdateDone` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker crafts a key consumed by DynamicPropertiesStore.getTokenUpdateDone that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in DynamicPropertiesStore.getTokenUpdateDone are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getTokenUpdateDone`
- Entrypoint: write via a path using DynamicPropertiesStore.getTokenUpdateDone
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getTokenUpdateDone` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by DynamicPropertiesStore.getTokenUpdateDone that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in DynamicPropertiesStore.getTokenUpdateDone are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
