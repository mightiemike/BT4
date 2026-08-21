# Q3427: DynamicPropertiesStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMinFrozenTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker calls a count/size path backed by DynamicPropertiesStore.getMinFrozenTime that iterates the whole store per request — to break the invariant that DynamicPropertiesStore.getMinFrozenTime answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMinFrozenTime`
- Entrypoint: query backed by DynamicPropertiesStore.getMinFrozenTime
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMinFrozenTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by DynamicPropertiesStore.getMinFrozenTime that iterates the whole store per request
- Invariant to test: DynamicPropertiesStore.getMinFrozenTime answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring DynamicPropertiesStore.getMinFrozenTime cost vs store size
