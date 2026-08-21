# Q616: MarketPairToPriceStore: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairToPriceStore.addNewPriceKey` in `chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java` — where the attacker uses MarketPairToPriceStore.addNewPriceKey to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in MarketPairToPriceStore.addNewPriceKey preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java` -> `MarketPairToPriceStore.addNewPriceKey`
- Entrypoint: broadcast exchange ops via MarketPairToPriceStore.addNewPriceKey
- Attacker controls: request/transaction/contract inputs to `MarketPairToPriceStore.addNewPriceKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses MarketPairToPriceStore.addNewPriceKey to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in MarketPairToPriceStore.addNewPriceKey preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
