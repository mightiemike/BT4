# Q1274: OperationRegistry: storage write miscount

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV15OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker triggers OperationRegistry.newTronV15OperationSet so storage refunds/writes are counted wrong, letting free or negative-cost writes — to break the invariant that storage energy in OperationRegistry.newTronV15OperationSet equals net slots changed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV15OperationSet`
- Entrypoint: contract toggling storage via OperationRegistry.newTronV15OperationSet
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV15OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers OperationRegistry.newTronV15OperationSet so storage refunds/writes are counted wrong, letting free or negative-cost writes
- Invariant to test: storage energy in OperationRegistry.newTronV15OperationSet equals net slots changed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test toggling slots and asserting net charge
