# Q2549: MarketOrderCapsule: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.getID` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker drives MarketOrderCapsule.getID usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in MarketOrderCapsule.getID never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.getID`
- Entrypoint: repeated ops via MarketOrderCapsule.getID
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.getID` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives MarketOrderCapsule.getID usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in MarketOrderCapsule.getID never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
