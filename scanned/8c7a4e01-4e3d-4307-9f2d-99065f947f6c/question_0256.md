# Q256: MarketOrderCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setID` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker shapes usage so MarketOrderCapsule.setID charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in MarketOrderCapsule.setID, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setID`
- Entrypoint: broadcast txs metered by MarketOrderCapsule.setID
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setID` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so MarketOrderCapsule.setID charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in MarketOrderCapsule.setID
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
