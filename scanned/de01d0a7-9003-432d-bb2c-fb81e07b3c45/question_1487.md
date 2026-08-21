# Q1487: TransactionRegister: signature verification bypass

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionRegister.registerActuator` in `actuator/src/main/java/org/tron/core/utils/TransactionRegister.java` — where the attacker submits a transaction whose TransactionRegister.registerActuator accepts a missing, malleable, or duplicated signature as satisfying permission weight — to break the invariant that TransactionRegister.registerActuator requires signatures recovering to distinct keys summing to threshold weight, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/TransactionRegister.java` -> `TransactionRegister.registerActuator`
- Entrypoint: broadcast a tx exercising TransactionRegister.registerActuator
- Attacker controls: request/transaction/contract inputs to `TransactionRegister.registerActuator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a transaction whose TransactionRegister.registerActuator accepts a missing, malleable, or duplicated signature as satisfying permission weight
- Invariant to test: TransactionRegister.registerActuator requires signatures recovering to distinct keys summing to threshold weight
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit with empty/duplicate sig sets asserting rejection
