# Q3305: MarketOrderCapsule: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.getID` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker repeatedly claims through MarketOrderCapsule.getID exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in MarketOrderCapsule.getID, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.getID`
- Entrypoint: many small claims via MarketOrderCapsule.getID
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.getID` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through MarketOrderCapsule.getID exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in MarketOrderCapsule.getID
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
