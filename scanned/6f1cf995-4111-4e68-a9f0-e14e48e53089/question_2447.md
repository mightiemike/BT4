# Q2447: OperationRegistry: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV13OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker uses OperationRegistry.newTronV13OperationSet to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in OperationRegistry.newTronV13OperationSet cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV13OperationSet`
- Entrypoint: CREATE/CREATE2 via OperationRegistry.newTronV13OperationSet
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV13OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses OperationRegistry.newTronV13OperationSet to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in OperationRegistry.newTronV13OperationSet cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
