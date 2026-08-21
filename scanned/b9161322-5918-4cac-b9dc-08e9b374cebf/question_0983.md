# Q983: OperationRegistry: memory expansion cost gap

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV13OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker forces OperationRegistry.newTronV13OperationSet to expand memory/return-data past what its gas formula charges — to break the invariant that memory/copy operations charge quadratic cost matching allocation, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV13OperationSet`
- Entrypoint: contract hitting OperationRegistry.newTronV13OperationSet with large offsets
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV13OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces OperationRegistry.newTronV13OperationSet to expand memory/return-data past what its gas formula charges
- Invariant to test: memory/copy operations charge quadratic cost matching allocation
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test with huge offset asserting cost >= allocation
