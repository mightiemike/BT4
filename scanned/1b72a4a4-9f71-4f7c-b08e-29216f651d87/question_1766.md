# Q1766: MarketPairPriceToOrderStore: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getNextKey` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker uses MarketPairPriceToOrderStore.getNextKey to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in MarketPairPriceToOrderStore.getNextKey preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getNextKey`
- Entrypoint: broadcast exchange ops via MarketPairPriceToOrderStore.getNextKey
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getNextKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses MarketPairPriceToOrderStore.getNextKey to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in MarketPairPriceToOrderStore.getNextKey preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
