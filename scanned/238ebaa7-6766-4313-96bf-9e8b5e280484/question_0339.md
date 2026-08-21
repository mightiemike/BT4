# Q339: EnergyProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `EnergyProcessor.calculateGlobalEnergyLimitV2` in `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` — where the attacker drives EnergyProcessor.calculateGlobalEnergyLimitV2 usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in EnergyProcessor.calculateGlobalEnergyLimitV2 never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` -> `EnergyProcessor.calculateGlobalEnergyLimitV2`
- Entrypoint: repeated ops via EnergyProcessor.calculateGlobalEnergyLimitV2
- Attacker controls: request/transaction/contract inputs to `EnergyProcessor.calculateGlobalEnergyLimitV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives EnergyProcessor.calculateGlobalEnergyLimitV2 usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in EnergyProcessor.calculateGlobalEnergyLimitV2 never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
