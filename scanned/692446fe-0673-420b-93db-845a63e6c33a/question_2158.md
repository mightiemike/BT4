# Q2158: MarketPairPriceToOrderStore: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairPriceToOrderStore.getPriceKeysList` in `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` — where the attacker repeatedly claims through MarketPairPriceToOrderStore.getPriceKeysList exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in MarketPairPriceToOrderStore.getPriceKeysList, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairPriceToOrderStore.java` -> `MarketPairPriceToOrderStore.getPriceKeysList`
- Entrypoint: many small claims via MarketPairPriceToOrderStore.getPriceKeysList
- Attacker controls: request/transaction/contract inputs to `MarketPairPriceToOrderStore.getPriceKeysList` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through MarketPairPriceToOrderStore.getPriceKeysList exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in MarketPairPriceToOrderStore.getPriceKeysList
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
