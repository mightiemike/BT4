# Q1305: ExchangeProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchangeToSupply` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker repeatedly claims through ExchangeProcessor.exchangeToSupply exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in ExchangeProcessor.exchangeToSupply, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchangeToSupply`
- Entrypoint: many small claims via ExchangeProcessor.exchangeToSupply
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchangeToSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through ExchangeProcessor.exchangeToSupply exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in ExchangeProcessor.exchangeToSupply
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
