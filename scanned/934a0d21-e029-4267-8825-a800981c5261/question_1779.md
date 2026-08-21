# Q1779: AccountPermissionUpdateActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountPermissionUpdateActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java` — where the attacker sizes amounts in AccountPermissionUpdateActuator so a subtraction in AccountPermissionUpdateActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in AccountPermissionUpdateActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java` -> `AccountPermissionUpdateActuator.execute`
- Entrypoint: broadcast AccountPermissionUpdateActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `AccountPermissionUpdateActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in AccountPermissionUpdateActuator so a subtraction in AccountPermissionUpdateActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in AccountPermissionUpdateActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
