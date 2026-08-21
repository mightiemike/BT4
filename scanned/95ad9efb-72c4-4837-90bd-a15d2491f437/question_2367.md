# Q2367: MarketOrderStore: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderStore.<primary method>` in `chainbase/src/main/java/org/tron/core/store/MarketOrderStore.java` — where the attacker repeatedly claims through MarketOrderStore.<primary method> exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in MarketOrderStore.<primary method>, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketOrderStore.java` -> `MarketOrderStore.<primary method>`
- Entrypoint: many small claims via MarketOrderStore.<primary method>
- Attacker controls: request/transaction/contract inputs to `MarketOrderStore.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through MarketOrderStore.<primary method> exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in MarketOrderStore.<primary method>
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
