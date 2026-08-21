# Q1494: EnergyProcessor: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `EnergyProcessor.calculateGlobalEnergyLimit` in `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` — where the attacker shapes usage so EnergyProcessor.calculateGlobalEnergyLimit charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in EnergyProcessor.calculateGlobalEnergyLimit, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java` -> `EnergyProcessor.calculateGlobalEnergyLimit`
- Entrypoint: broadcast txs metered by EnergyProcessor.calculateGlobalEnergyLimit
- Attacker controls: request/transaction/contract inputs to `EnergyProcessor.calculateGlobalEnergyLimit` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so EnergyProcessor.calculateGlobalEnergyLimit charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in EnergyProcessor.calculateGlobalEnergyLimit
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
