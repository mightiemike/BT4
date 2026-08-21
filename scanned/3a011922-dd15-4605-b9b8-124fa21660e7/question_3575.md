# Q3575: OperationRegistry: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV12OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker crafts a sequence reaching OperationRegistry.newTronV12OperationSet where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in OperationRegistry.newTronV12OperationSet, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV12OperationSet`
- Entrypoint: deploy/trigger a contract exercising OperationRegistry.newTronV12OperationSet
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV12OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching OperationRegistry.newTronV12OperationSet where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in OperationRegistry.newTronV12OperationSet
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
