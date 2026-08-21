# Q3049: MarketPairPriceToOrderStore: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getNextKey` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker drives MarketPairPriceToOrderStore.getNextKey usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in MarketPairPriceToOrderStore.getNextKey never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getNextKey`
- Entrypoint: repeated ops via MarketPairPriceToOrderStore.getNextKey
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getNextKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives MarketPairPriceToOrderStore.getNextKey usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in MarketPairPriceToOrderStore.getNextKey never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
