# Q1547: SafeExchangeProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `SafeExchangeProcessor.exchangeToSupply` in `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` — where the attacker repeatedly claims through SafeExchangeProcessor.exchangeToSupply exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in SafeExchangeProcessor.exchangeToSupply, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` -> `SafeExchangeProcessor.exchangeToSupply`
- Entrypoint: many small claims via SafeExchangeProcessor.exchangeToSupply
- Attacker controls: request/transaction/contract inputs to `SafeExchangeProcessor.exchangeToSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through SafeExchangeProcessor.exchangeToSupply exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in SafeExchangeProcessor.exchangeToSupply
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
