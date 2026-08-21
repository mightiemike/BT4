# Q1311: EnergyProcessor: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `EnergyProcessor.calculateGlobalEnergyLimitV2` in `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` — where the attacker uses EnergyProcessor.calculateGlobalEnergyLimitV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in EnergyProcessor.calculateGlobalEnergyLimitV2 preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` -> `EnergyProcessor.calculateGlobalEnergyLimitV2`
- Entrypoint: broadcast exchange ops via EnergyProcessor.calculateGlobalEnergyLimitV2
- Attacker controls: request/transaction/contract inputs to `EnergyProcessor.calculateGlobalEnergyLimitV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses EnergyProcessor.calculateGlobalEnergyLimitV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in EnergyProcessor.calculateGlobalEnergyLimitV2 preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
