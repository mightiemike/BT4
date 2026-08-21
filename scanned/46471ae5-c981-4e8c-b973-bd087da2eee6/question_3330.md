# Q3330: MarketPairToPriceStore: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairToPriceStore.addNewPriceKey` in `chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java` — where the attacker submits an order via MarketPairToPriceStore.addNewPriceKey whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in MarketPairToPriceStore.addNewPriceKey never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java` -> `MarketPairToPriceStore.addNewPriceKey`
- Entrypoint: broadcast a market order to MarketPairToPriceStore.addNewPriceKey
- Attacker controls: request/transaction/contract inputs to `MarketPairToPriceStore.addNewPriceKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via MarketPairToPriceStore.addNewPriceKey whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in MarketPairToPriceStore.addNewPriceKey never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
