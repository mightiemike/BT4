# Q362: MarketPairPriceToOrderStore: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getKeysNext` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker uses MarketPairPriceToOrderStore.getKeysNext to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in MarketPairPriceToOrderStore.getKeysNext preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getKeysNext`
- Entrypoint: broadcast exchange ops via MarketPairPriceToOrderStore.getKeysNext
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getKeysNext` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses MarketPairPriceToOrderStore.getKeysNext to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in MarketPairPriceToOrderStore.getKeysNext preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
