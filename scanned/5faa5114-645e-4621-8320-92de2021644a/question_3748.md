# Q3748: MarketOrderCapsule: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.getOwnerAddress` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker uses MarketOrderCapsule.getOwnerAddress to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in MarketOrderCapsule.getOwnerAddress preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.getOwnerAddress`
- Entrypoint: broadcast exchange ops via MarketOrderCapsule.getOwnerAddress
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.getOwnerAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses MarketOrderCapsule.getOwnerAddress to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in MarketOrderCapsule.getOwnerAddress preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
