# Q3067: MarketOrderCapsule: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderCapsule.getID` in `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` — where the attacker submits an order via MarketOrderCapsule.getID whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in MarketOrderCapsule.getID never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java` -> `MarketOrderCapsule.getID`
- Entrypoint: broadcast a market order to MarketOrderCapsule.getID
- Attacker controls: request/transaction/contract inputs to `MarketOrderCapsule.getID` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via MarketOrderCapsule.getID whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in MarketOrderCapsule.getID never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
