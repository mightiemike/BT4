# Q3838: MarketOrderCapsule: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setCreateTime` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker repeatedly claims through MarketOrderCapsule.setCreateTime exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in MarketOrderCapsule.setCreateTime, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setCreateTime`
- Entrypoint: many small claims via MarketOrderCapsule.setCreateTime
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setCreateTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through MarketOrderCapsule.setCreateTime exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in MarketOrderCapsule.setCreateTime
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
