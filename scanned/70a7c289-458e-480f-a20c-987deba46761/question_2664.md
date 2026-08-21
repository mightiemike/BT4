# Q2664: DelegatedResourceAccountIndexCapsule: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.removeToAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker times DelegatedResourceAccountIndexCapsule.removeToAccount to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that DelegatedResourceAccountIndexCapsule.removeToAccount reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.removeToAccount`
- Entrypoint: broadcast metered by DelegatedResourceAccountIndexCapsule.removeToAccount across a window boundary
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.removeToAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times DelegatedResourceAccountIndexCapsule.removeToAccount to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: DelegatedResourceAccountIndexCapsule.removeToAccount reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
