# Q2556: DelegateResourceActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegateResourceActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java` — where the attacker sizes amounts in DelegateResourceActuator so a subtraction in DelegateResourceActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in DelegateResourceActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java` -> `DelegateResourceActuator.execute`
- Entrypoint: broadcast DelegateResourceActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `DelegateResourceActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in DelegateResourceActuator so a subtraction in DelegateResourceActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in DelegateResourceActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
