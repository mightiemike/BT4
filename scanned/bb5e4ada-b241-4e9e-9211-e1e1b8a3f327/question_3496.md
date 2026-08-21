# Q3496: RepositoryImpl: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `RepositoryImpl.createRoot` in `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` — where the attacker reenters RepositoryImpl.createRoot using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that RepositoryImpl.createRoot debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` -> `RepositoryImpl.createRoot`
- Entrypoint: reentrant contract exercising RepositoryImpl.createRoot
- Attacker controls: request/transaction/contract inputs to `RepositoryImpl.createRoot` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters RepositoryImpl.createRoot using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: RepositoryImpl.createRoot debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
