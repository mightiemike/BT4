# Q2454: MarketOrderCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.getOwnerAddress` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker shapes usage so MarketOrderCapsule.getOwnerAddress charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in MarketOrderCapsule.getOwnerAddress, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.getOwnerAddress`
- Entrypoint: broadcast txs metered by MarketOrderCapsule.getOwnerAddress
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.getOwnerAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so MarketOrderCapsule.getOwnerAddress charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in MarketOrderCapsule.getOwnerAddress
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
