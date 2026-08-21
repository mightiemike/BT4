# Q771: ResourceProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.hardenCalculation` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker repeatedly claims through ResourceProcessor.hardenCalculation exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in ResourceProcessor.hardenCalculation, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.hardenCalculation`
- Entrypoint: many small claims via ResourceProcessor.hardenCalculation
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.hardenCalculation` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through ResourceProcessor.hardenCalculation exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in ResourceProcessor.hardenCalculation
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
