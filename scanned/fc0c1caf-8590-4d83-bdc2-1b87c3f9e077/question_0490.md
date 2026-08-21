# Q490: DynamicPropertiesStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getMaxFrozenTime` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker inflates the revoking/undo set through operations touching DynamicPropertiesStore.getMaxFrozenTime, growing memory per block — to break the invariant that undo state in DynamicPropertiesStore.getMaxFrozenTime is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getMaxFrozenTime`
- Entrypoint: many state writes via DynamicPropertiesStore.getMaxFrozenTime
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getMaxFrozenTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching DynamicPropertiesStore.getMaxFrozenTime, growing memory per block
- Invariant to test: undo state in DynamicPropertiesStore.getMaxFrozenTime is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
