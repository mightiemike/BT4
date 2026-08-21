# Q3443: MarketPairPriceToOrderStore: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getPriceKeysList` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker drives MarketPairPriceToOrderStore.getPriceKeysList usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in MarketPairPriceToOrderStore.getPriceKeysList never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getPriceKeysList`
- Entrypoint: repeated ops via MarketPairPriceToOrderStore.getPriceKeysList
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getPriceKeysList` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives MarketPairPriceToOrderStore.getPriceKeysList usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in MarketPairPriceToOrderStore.getPriceKeysList never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
