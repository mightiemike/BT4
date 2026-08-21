# Q2504: EnergyProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `EnergyProcessor.calculateGlobalEnergyLimit` in `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` — where the attacker drives EnergyProcessor.calculateGlobalEnergyLimit usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in EnergyProcessor.calculateGlobalEnergyLimit never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` -> `EnergyProcessor.calculateGlobalEnergyLimit`
- Entrypoint: repeated ops via EnergyProcessor.calculateGlobalEnergyLimit
- Attacker controls: request/transaction/contract inputs to `EnergyProcessor.calculateGlobalEnergyLimit` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives EnergyProcessor.calculateGlobalEnergyLimit usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in EnergyProcessor.calculateGlobalEnergyLimit never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
