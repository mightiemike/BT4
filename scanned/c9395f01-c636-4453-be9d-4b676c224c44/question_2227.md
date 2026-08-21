# Q2227: DelegatedResourceAccountIndexCapsule: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.addToAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker times DelegatedResourceAccountIndexCapsule.addToAccount to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that DelegatedResourceAccountIndexCapsule.addToAccount reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.addToAccount`
- Entrypoint: broadcast metered by DelegatedResourceAccountIndexCapsule.addToAccount across a window boundary
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.addToAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times DelegatedResourceAccountIndexCapsule.addToAccount to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: DelegatedResourceAccountIndexCapsule.addToAccount reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
