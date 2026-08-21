# Q3664: MarketPairPriceToOrderStore: market order price/amount overflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getKeysNext` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker submits an order via MarketPairPriceToOrderStore.getKeysNext whose price*amount overflows or rounds to seize value from the book — to break the invariant that order math in MarketPairPriceToOrderStore.getKeysNext never overflows or mismatches sell/buy units, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getKeysNext`
- Entrypoint: broadcast a market order to MarketPairPriceToOrderStore.getKeysNext
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getKeysNext` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits an order via MarketPairPriceToOrderStore.getKeysNext whose price*amount overflows or rounds to seize value from the book
- Invariant to test: order math in MarketPairPriceToOrderStore.getKeysNext never overflows or mismatches sell/buy units
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with extreme price/amount asserting no overflow
