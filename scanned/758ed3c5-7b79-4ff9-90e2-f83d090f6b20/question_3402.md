# Q3402: MessageCall: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getInDataSize` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker reenters MessageCall.getInDataSize using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that MessageCall.getInDataSize debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getInDataSize`
- Entrypoint: reentrant contract exercising MessageCall.getInDataSize
- Attacker controls: request/transaction/contract inputs to `MessageCall.getInDataSize` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters MessageCall.getInDataSize using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: MessageCall.getInDataSize debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
