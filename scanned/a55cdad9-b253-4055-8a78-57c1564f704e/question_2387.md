# Q2387: ResourceProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.unDelegateIncreaseV2` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker repeatedly claims through ResourceProcessor.unDelegateIncreaseV2 exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in ResourceProcessor.unDelegateIncreaseV2, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.unDelegateIncreaseV2`
- Entrypoint: many small claims via ResourceProcessor.unDelegateIncreaseV2
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.unDelegateIncreaseV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through ResourceProcessor.unDelegateIncreaseV2 exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in ResourceProcessor.unDelegateIncreaseV2
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
