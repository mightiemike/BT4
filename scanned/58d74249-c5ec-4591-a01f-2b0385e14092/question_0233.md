# Q233: MarketOrderCapsule: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setOwnerAddress` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker repeatedly claims through MarketOrderCapsule.setOwnerAddress exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in MarketOrderCapsule.setOwnerAddress, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setOwnerAddress`
- Entrypoint: many small claims via MarketOrderCapsule.setOwnerAddress
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setOwnerAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through MarketOrderCapsule.setOwnerAddress exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in MarketOrderCapsule.setOwnerAddress
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
