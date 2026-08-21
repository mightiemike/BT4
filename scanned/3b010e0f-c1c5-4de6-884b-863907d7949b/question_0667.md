# Q667: ProgramInvokeFactory: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `ProgramInvokeFactory.createProgramInvoke` in `actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeFactory.java` — where the attacker reenters ProgramInvokeFactory.createProgramInvoke using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that ProgramInvokeFactory.createProgramInvoke debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeFactory.java` -> `ProgramInvokeFactory.createProgramInvoke`
- Entrypoint: reentrant contract exercising ProgramInvokeFactory.createProgramInvoke
- Attacker controls: request/transaction/contract inputs to `ProgramInvokeFactory.createProgramInvoke` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters ProgramInvokeFactory.createProgramInvoke using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: ProgramInvokeFactory.createProgramInvoke debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
