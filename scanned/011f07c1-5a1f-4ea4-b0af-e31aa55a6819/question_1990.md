# Q1990: ResourceProcessor: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.calculateGlobalLimitV2` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker times ResourceProcessor.calculateGlobalLimitV2 to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that ResourceProcessor.calculateGlobalLimitV2 reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.calculateGlobalLimitV2`
- Entrypoint: broadcast metered by ResourceProcessor.calculateGlobalLimitV2 across a window boundary
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.calculateGlobalLimitV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times ResourceProcessor.calculateGlobalLimitV2 to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: ResourceProcessor.calculateGlobalLimitV2 reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
