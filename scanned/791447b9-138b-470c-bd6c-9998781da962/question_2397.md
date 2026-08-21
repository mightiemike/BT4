# Q2397: MarketPairPriceToOrderStore: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getPriceKeysList` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker inflates vote weight through MarketPairPriceToOrderStore.getPriceKeysList beyond frozen stake — to break the invariant that votes counted in MarketPairPriceToOrderStore.getPriceKeysList never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getPriceKeysList`
- Entrypoint: broadcast votes via MarketPairPriceToOrderStore.getPriceKeysList
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getPriceKeysList` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through MarketPairPriceToOrderStore.getPriceKeysList beyond frozen stake
- Invariant to test: votes counted in MarketPairPriceToOrderStore.getPriceKeysList never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
