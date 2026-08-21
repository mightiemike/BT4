# Q3265: OperationRegistry: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV14OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker uses OperationRegistry.newTronV14OperationSet to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in OperationRegistry.newTronV14OperationSet cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV14OperationSet`
- Entrypoint: CREATE/CREATE2 via OperationRegistry.newTronV14OperationSet
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV14OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses OperationRegistry.newTronV14OperationSet to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in OperationRegistry.newTronV14OperationSet cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
