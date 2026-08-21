# Q2205: UpdateAccountActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAccountActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java` — where the attacker sizes amounts in UpdateAccountActuator so a subtraction in UpdateAccountActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in UpdateAccountActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java` -> `UpdateAccountActuator.execute`
- Entrypoint: broadcast UpdateAccountActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `UpdateAccountActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in UpdateAccountActuator so a subtraction in UpdateAccountActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in UpdateAccountActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
