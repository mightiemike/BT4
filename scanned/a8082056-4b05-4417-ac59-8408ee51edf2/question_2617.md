# Q2617: VMActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VMActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/VMActuator.java` — where the attacker sizes amounts in VMActuator so a subtraction in VMActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in VMActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/VMActuator.java` -> `VMActuator.execute`
- Entrypoint: broadcast VMActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `VMActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in VMActuator so a subtraction in VMActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in VMActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
