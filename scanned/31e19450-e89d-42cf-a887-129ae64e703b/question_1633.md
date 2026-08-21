# Q1633: DelegatedResourceCapsule: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.createDbKey` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker times DelegatedResourceCapsule.createDbKey to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that DelegatedResourceCapsule.createDbKey reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.createDbKey`
- Entrypoint: broadcast metered by DelegatedResourceCapsule.createDbKey across a window boundary
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.createDbKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times DelegatedResourceCapsule.createDbKey to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: DelegatedResourceCapsule.createDbKey reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
