# Q2926: OperationRegistry: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV15OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker crafts a sequence reaching OperationRegistry.newTronV15OperationSet where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in OperationRegistry.newTronV15OperationSet, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV15OperationSet`
- Entrypoint: deploy/trigger a contract exercising OperationRegistry.newTronV15OperationSet
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV15OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching OperationRegistry.newTronV15OperationSet where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in OperationRegistry.newTronV15OperationSet
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
