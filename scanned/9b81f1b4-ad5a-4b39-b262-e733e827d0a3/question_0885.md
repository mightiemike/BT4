# Q885: DynamicPropertiesStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMaxFrozenSupplyNumber` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker calls a count/size path backed by DynamicPropertiesStore.getMaxFrozenSupplyNumber that iterates the whole store per request — to break the invariant that DynamicPropertiesStore.getMaxFrozenSupplyNumber answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMaxFrozenSupplyNumber`
- Entrypoint: query backed by DynamicPropertiesStore.getMaxFrozenSupplyNumber
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMaxFrozenSupplyNumber` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by DynamicPropertiesStore.getMaxFrozenSupplyNumber that iterates the whole store per request
- Invariant to test: DynamicPropertiesStore.getMaxFrozenSupplyNumber answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring DynamicPropertiesStore.getMaxFrozenSupplyNumber cost vs store size
