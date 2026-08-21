# Q1647: MarketOrderCapsule: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.setCreateTime` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker uses MarketOrderCapsule.setCreateTime to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in MarketOrderCapsule.setCreateTime preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.setCreateTime`
- Entrypoint: broadcast exchange ops via MarketOrderCapsule.setCreateTime
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.setCreateTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses MarketOrderCapsule.setCreateTime to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in MarketOrderCapsule.setCreateTime preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
