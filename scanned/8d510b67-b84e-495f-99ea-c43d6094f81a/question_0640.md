# Q640: VM: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VM.play` in `actuator/src/main/java/org/tron/core/vm/VM.java` — where the attacker reenters VM.play using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that VM.play debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VM.java` -> `VM.play`
- Entrypoint: reentrant contract exercising VM.play
- Attacker controls: request/transaction/contract inputs to `VM.play` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters VM.play using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: VM.play debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
