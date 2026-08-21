# Q1268: ResourceProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.unDelegateIncrease` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker repeatedly claims through ResourceProcessor.unDelegateIncrease exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in ResourceProcessor.unDelegateIncrease, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.unDelegateIncrease`
- Entrypoint: many small claims via ResourceProcessor.unDelegateIncrease
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.unDelegateIncrease` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through ResourceProcessor.unDelegateIncrease exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in ResourceProcessor.unDelegateIncrease
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
