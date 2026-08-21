# Q2796: MarketOrderCapsule: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setOwnerAddress` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker shapes usage so MarketOrderCapsule.setOwnerAddress charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in MarketOrderCapsule.setOwnerAddress, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setOwnerAddress`
- Entrypoint: broadcast txs metered by MarketOrderCapsule.setOwnerAddress
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setOwnerAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so MarketOrderCapsule.setOwnerAddress charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in MarketOrderCapsule.setOwnerAddress
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
