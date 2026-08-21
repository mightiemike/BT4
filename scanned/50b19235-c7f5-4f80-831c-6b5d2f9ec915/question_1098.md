# Q1098: DynamicPropertiesStore: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMinFrozenSupplyTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker triggers DynamicPropertiesStore.getMinFrozenSupplyTime paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in DynamicPropertiesStore.getMinFrozenSupplyTime is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMinFrozenSupplyTime`
- Entrypoint: repeated queries via DynamicPropertiesStore.getMinFrozenSupplyTime
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMinFrozenSupplyTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers DynamicPropertiesStore.getMinFrozenSupplyTime paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in DynamicPropertiesStore.getMinFrozenSupplyTime is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress DynamicPropertiesStore.getMinFrozenSupplyTime and watch handle/heap growth
