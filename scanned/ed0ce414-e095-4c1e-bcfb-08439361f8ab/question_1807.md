# Q1807: TransactionRegister: tx hash / dedup collision

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionRegister.registerActuator` in `actuator/src/main/java/org/tron/core/utils/TransactionRegister.java` — where the attacker crafts two distinct transactions colliding on the id/cache key checked by TransactionRegister.registerActuator, evicting or replaying one — to break the invariant that distinct transactions have distinct dedup keys in TransactionRegister.registerActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/TransactionRegister.java` -> `TransactionRegister.registerActuator`
- Entrypoint: broadcast colliding txs to TransactionRegister.registerActuator
- Attacker controls: request/transaction/contract inputs to `TransactionRegister.registerActuator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts two distinct transactions colliding on the id/cache key checked by TransactionRegister.registerActuator, evicting or replaying one
- Invariant to test: distinct transactions have distinct dedup keys in TransactionRegister.registerActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit constructing id-collision pair asserting both distinct
