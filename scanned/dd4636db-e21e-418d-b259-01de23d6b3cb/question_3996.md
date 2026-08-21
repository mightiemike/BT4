# Q3996: EnergyProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `EnergyProcessor.calculateGlobalEnergyLimitV2` in `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` — where the attacker repeatedly claims through EnergyProcessor.calculateGlobalEnergyLimitV2 exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in EnergyProcessor.calculateGlobalEnergyLimitV2, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` -> `EnergyProcessor.calculateGlobalEnergyLimitV2`
- Entrypoint: many small claims via EnergyProcessor.calculateGlobalEnergyLimitV2
- Attacker controls: request/transaction/contract inputs to `EnergyProcessor.calculateGlobalEnergyLimitV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through EnergyProcessor.calculateGlobalEnergyLimitV2 exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in EnergyProcessor.calculateGlobalEnergyLimitV2
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
