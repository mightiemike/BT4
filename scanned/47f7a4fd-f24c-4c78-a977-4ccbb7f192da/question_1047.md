# Q1047: MarketOrderCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setOwnerAddress` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker inflates vote weight through MarketOrderCapsule.setOwnerAddress beyond frozen stake — to break the invariant that votes counted in MarketOrderCapsule.setOwnerAddress never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setOwnerAddress`
- Entrypoint: broadcast votes via MarketOrderCapsule.setOwnerAddress
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setOwnerAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through MarketOrderCapsule.setOwnerAddress beyond frozen stake
- Invariant to test: votes counted in MarketOrderCapsule.setOwnerAddress never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
