# Q834: Storage: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Storage.put` in `actuator/src/main/java/org/tron/core/vm/program/Storage.java` — where the attacker reenters Storage.put using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that Storage.put debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Storage.java` -> `Storage.put`
- Entrypoint: reentrant contract exercising Storage.put
- Attacker controls: request/transaction/contract inputs to `Storage.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters Storage.put using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: Storage.put debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
