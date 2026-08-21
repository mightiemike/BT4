# Q3251: MarketPairPriceToOrderStore: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getKeysNext` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker drives MarketPairPriceToOrderStore.getKeysNext usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in MarketPairPriceToOrderStore.getKeysNext never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getKeysNext`
- Entrypoint: repeated ops via MarketPairPriceToOrderStore.getKeysNext
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getKeysNext` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives MarketPairPriceToOrderStore.getKeysNext usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in MarketPairPriceToOrderStore.getKeysNext never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
