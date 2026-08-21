# Q627: MarketOrderCapsule: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setID` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker times MarketOrderCapsule.setID to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that MarketOrderCapsule.setID reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setID`
- Entrypoint: broadcast metered by MarketOrderCapsule.setID across a window boundary
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setID` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times MarketOrderCapsule.setID to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: MarketOrderCapsule.setID reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
