# Q903: MarketOrderCapsule: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setOwnerAddress` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker times MarketOrderCapsule.setOwnerAddress to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that MarketOrderCapsule.setOwnerAddress reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setOwnerAddress`
- Entrypoint: broadcast metered by MarketOrderCapsule.setOwnerAddress across a window boundary
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setOwnerAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times MarketOrderCapsule.setOwnerAddress to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: MarketOrderCapsule.setOwnerAddress reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
