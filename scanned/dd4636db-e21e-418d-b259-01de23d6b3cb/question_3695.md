# Q3695: MarketPairPriceToOrderStore: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getKeysNext` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker inflates vote weight through MarketPairPriceToOrderStore.getKeysNext beyond frozen stake — to break the invariant that votes counted in MarketPairPriceToOrderStore.getKeysNext never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getKeysNext`
- Entrypoint: broadcast votes via MarketPairPriceToOrderStore.getKeysNext
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getKeysNext` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through MarketPairPriceToOrderStore.getKeysNext beyond frozen stake
- Invariant to test: votes counted in MarketPairPriceToOrderStore.getKeysNext never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
