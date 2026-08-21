# Q1849: TransactionRegister: expiration / ref-block replay

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionRegister.registerActuator` in `actuator/src/main/java/org/tron/core/utils/TransactionRegister.java` — where the attacker replays a transaction past its intended window because TransactionRegister.registerActuator mis-checks expiration or ref-block — to break the invariant that TransactionRegister.registerActuator rejects expired or wrong-ref-block transactions, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/TransactionRegister.java` -> `TransactionRegister.registerActuator`
- Entrypoint: rebroadcast a tx through TransactionRegister.registerActuator
- Attacker controls: request/transaction/contract inputs to `TransactionRegister.registerActuator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays a transaction past its intended window because TransactionRegister.registerActuator mis-checks expiration or ref-block
- Invariant to test: TransactionRegister.registerActuator rejects expired or wrong-ref-block transactions
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit replaying at expiration boundary asserting rejection
