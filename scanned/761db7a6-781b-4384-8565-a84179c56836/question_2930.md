# Q2930: MarketOrderCapsule: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.getID` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker inflates vote weight through MarketOrderCapsule.getID beyond frozen stake — to break the invariant that votes counted in MarketOrderCapsule.getID never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.getID`
- Entrypoint: broadcast votes via MarketOrderCapsule.getID
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.getID` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through MarketOrderCapsule.getID beyond frozen stake
- Invariant to test: votes counted in MarketOrderCapsule.getID never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
