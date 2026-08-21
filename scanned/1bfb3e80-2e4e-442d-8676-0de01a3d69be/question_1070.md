# Q1070: DynamicPropertiesStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMinFrozenSupplyTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker calls a count/size path backed by DynamicPropertiesStore.getMinFrozenSupplyTime that iterates the whole store per request — to break the invariant that DynamicPropertiesStore.getMinFrozenSupplyTime answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMinFrozenSupplyTime`
- Entrypoint: query backed by DynamicPropertiesStore.getMinFrozenSupplyTime
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMinFrozenSupplyTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by DynamicPropertiesStore.getMinFrozenSupplyTime that iterates the whole store per request
- Invariant to test: DynamicPropertiesStore.getMinFrozenSupplyTime answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring DynamicPropertiesStore.getMinFrozenSupplyTime cost vs store size
