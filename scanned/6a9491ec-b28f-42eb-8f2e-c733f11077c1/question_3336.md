# Q3336: Memory: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Memory.extend` in `actuator/src/main/java/org/tron/core/vm/program/Memory.java` — where the attacker reenters Memory.extend using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that Memory.extend debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Memory.java` -> `Memory.extend`
- Entrypoint: reentrant contract exercising Memory.extend
- Attacker controls: request/transaction/contract inputs to `Memory.extend` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters Memory.extend using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: Memory.extend debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
