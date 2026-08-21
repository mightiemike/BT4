# Q238: MarketPairToPriceStore: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketPairToPriceStore.addNewPriceKey` in `chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java` — where the attacker repeatedly claims through MarketPairToPriceStore.addNewPriceKey exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in MarketPairToPriceStore.addNewPriceKey, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketPairToPriceStore.java` -> `MarketPairToPriceStore.addNewPriceKey`
- Entrypoint: many small claims via MarketPairToPriceStore.addNewPriceKey
- Attacker controls: request/transaction/contract inputs to `MarketPairToPriceStore.addNewPriceKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through MarketPairToPriceStore.addNewPriceKey exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in MarketPairToPriceStore.addNewPriceKey
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
