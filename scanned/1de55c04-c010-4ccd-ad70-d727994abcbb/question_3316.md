# Q3316: DynamicPropertiesStore: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getTokenIdNum` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker triggers DynamicPropertiesStore.getTokenIdNum paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in DynamicPropertiesStore.getTokenIdNum is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getTokenIdNum`
- Entrypoint: repeated queries via DynamicPropertiesStore.getTokenIdNum
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getTokenIdNum` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers DynamicPropertiesStore.getTokenIdNum paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in DynamicPropertiesStore.getTokenIdNum is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress DynamicPropertiesStore.getTokenIdNum and watch handle/heap growth
