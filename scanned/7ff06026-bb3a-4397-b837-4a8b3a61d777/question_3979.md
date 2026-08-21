# Q3979: DelegationStore: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.buildVoteKey` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker times DelegationStore.buildVoteKey to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that DelegationStore.buildVoteKey reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.buildVoteKey`
- Entrypoint: broadcast metered by DelegationStore.buildVoteKey across a window boundary
- Attacker controls: request/transaction/contract inputs to `DelegationStore.buildVoteKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times DelegationStore.buildVoteKey to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: DelegationStore.buildVoteKey reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
