# Q1712: DelegationStore: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.addReward` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker times DelegationStore.addReward to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that DelegationStore.addReward reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.addReward`
- Entrypoint: broadcast metered by DelegationStore.addReward across a window boundary
- Attacker controls: request/transaction/contract inputs to `DelegationStore.addReward` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times DelegationStore.addReward to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: DelegationStore.addReward reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
