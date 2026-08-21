# Q2789: DynamicPropertiesStore: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMinFrozenTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker triggers DynamicPropertiesStore.getMinFrozenTime paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in DynamicPropertiesStore.getMinFrozenTime is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMinFrozenTime`
- Entrypoint: repeated queries via DynamicPropertiesStore.getMinFrozenTime
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMinFrozenTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers DynamicPropertiesStore.getMinFrozenTime paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in DynamicPropertiesStore.getMinFrozenTime is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress DynamicPropertiesStore.getMinFrozenTime and watch handle/heap growth
