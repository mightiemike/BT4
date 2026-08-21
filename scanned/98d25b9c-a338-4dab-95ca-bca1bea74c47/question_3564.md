# Q3564: OperationRegistry: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `OperationRegistry.newTronV15OperationSet` in `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` — where the attacker reenters OperationRegistry.newTronV15OperationSet using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that OperationRegistry.newTronV15OperationSet debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/OperationRegistry.java` -> `OperationRegistry.newTronV15OperationSet`
- Entrypoint: reentrant contract exercising OperationRegistry.newTronV15OperationSet
- Attacker controls: request/transaction/contract inputs to `OperationRegistry.newTronV15OperationSet` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters OperationRegistry.newTronV15OperationSet using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: OperationRegistry.newTronV15OperationSet debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
