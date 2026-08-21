# Q3552: DynamicPropertiesStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMaxFrozenSupplyNumber` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker inflates the revoking/undo set through operations touching DynamicPropertiesStore.getMaxFrozenSupplyNumber, growing memory per block — to break the invariant that undo state in DynamicPropertiesStore.getMaxFrozenSupplyNumber is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMaxFrozenSupplyNumber`
- Entrypoint: many state writes via DynamicPropertiesStore.getMaxFrozenSupplyNumber
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMaxFrozenSupplyNumber` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching DynamicPropertiesStore.getMaxFrozenSupplyNumber, growing memory per block
- Invariant to test: undo state in DynamicPropertiesStore.getMaxFrozenSupplyNumber is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
