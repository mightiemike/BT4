# Q270: MarketOrderCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.getID` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker shapes usage so MarketOrderCapsule.getID charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in MarketOrderCapsule.getID, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.getID`
- Entrypoint: broadcast txs metered by MarketOrderCapsule.getID
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.getID` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so MarketOrderCapsule.getID charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in MarketOrderCapsule.getID
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
