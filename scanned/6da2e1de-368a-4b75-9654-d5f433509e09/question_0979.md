# Q979: DynamicPropertiesStore: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getWitnessAllowanceFrozenTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker triggers DynamicPropertiesStore.getWitnessAllowanceFrozenTime paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in DynamicPropertiesStore.getWitnessAllowanceFrozenTime is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getWitnessAllowanceFrozenTime`
- Entrypoint: repeated queries via DynamicPropertiesStore.getWitnessAllowanceFrozenTime
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getWitnessAllowanceFrozenTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers DynamicPropertiesStore.getWitnessAllowanceFrozenTime paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in DynamicPropertiesStore.getWitnessAllowanceFrozenTime is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress DynamicPropertiesStore.getWitnessAllowanceFrozenTime and watch handle/heap growth
