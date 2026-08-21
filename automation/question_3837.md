# Q3837: MarketOrderCapsule: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.getOwnerAddress` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker drives MarketOrderCapsule.getOwnerAddress usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in MarketOrderCapsule.getOwnerAddress never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.getOwnerAddress`
- Entrypoint: repeated ops via MarketOrderCapsule.getOwnerAddress
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.getOwnerAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives MarketOrderCapsule.getOwnerAddress usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in MarketOrderCapsule.getOwnerAddress never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
