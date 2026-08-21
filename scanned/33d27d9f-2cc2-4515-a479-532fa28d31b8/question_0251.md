# Q251: DynamicPropertiesStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `DynamicPropertiesStore.getTokenUpdateDone` in `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` — where the attacker inflates the revoking/undo set through operations touching DynamicPropertiesStore.getTokenUpdateDone, growing memory per block — to break the invariant that undo state in DynamicPropertiesStore.getTokenUpdateDone is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java` -> `DynamicPropertiesStore.getTokenUpdateDone`
- Entrypoint: many state writes via DynamicPropertiesStore.getTokenUpdateDone
- Attacker controls: request/transaction/contract inputs to `DynamicPropertiesStore.getTokenUpdateDone` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching DynamicPropertiesStore.getTokenUpdateDone, growing memory per block
- Invariant to test: undo state in DynamicPropertiesStore.getTokenUpdateDone is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
