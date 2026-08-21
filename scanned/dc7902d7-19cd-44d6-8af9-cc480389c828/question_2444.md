# Q2444: Storage: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Storage.commit` in `actuator/src/main/java/org/tron/core/vm/program/Storage.java` — where the attacker reenters Storage.commit using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that Storage.commit debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Storage.java` -> `Storage.commit`
- Entrypoint: reentrant contract exercising Storage.commit
- Attacker controls: request/transaction/contract inputs to `Storage.commit` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters Storage.commit using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: Storage.commit debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
