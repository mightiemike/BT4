# Q2622: Memory: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Memory.readWord` in `actuator/src/main/java/org/tron/core/vm/program/Memory.java` — where the attacker reenters Memory.readWord using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that Memory.readWord debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Memory.java` -> `Memory.readWord`
- Entrypoint: reentrant contract exercising Memory.readWord
- Attacker controls: request/transaction/contract inputs to `Memory.readWord` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters Memory.readWord using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: Memory.readWord debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
