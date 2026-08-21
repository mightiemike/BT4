# Q2294: EnergyProcessor: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `EnergyProcessor.calculateGlobalEnergyLimit` in `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` — where the attacker uses EnergyProcessor.calculateGlobalEnergyLimit to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in EnergyProcessor.calculateGlobalEnergyLimit preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` -> `EnergyProcessor.calculateGlobalEnergyLimit`
- Entrypoint: broadcast exchange ops via EnergyProcessor.calculateGlobalEnergyLimit
- Attacker controls: request/transaction/contract inputs to `EnergyProcessor.calculateGlobalEnergyLimit` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses EnergyProcessor.calculateGlobalEnergyLimit to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in EnergyProcessor.calculateGlobalEnergyLimit preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
