# Q933: ExchangeProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchange` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker repeatedly claims through ExchangeProcessor.exchange exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in ExchangeProcessor.exchange, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchange`
- Entrypoint: many small claims via ExchangeProcessor.exchange
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchange` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through ExchangeProcessor.exchange exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in ExchangeProcessor.exchange
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
