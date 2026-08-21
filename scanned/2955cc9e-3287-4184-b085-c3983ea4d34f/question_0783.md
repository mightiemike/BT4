# Q783: AbstractExchangeActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AbstractExchangeActuator.allowHarden` in `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` — where the attacker sizes amounts in AbstractExchangeActuator so a subtraction in AbstractExchangeActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in AbstractExchangeActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` -> `AbstractExchangeActuator.allowHarden`
- Entrypoint: broadcast AbstractExchangeActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `AbstractExchangeActuator.allowHarden` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in AbstractExchangeActuator so a subtraction in AbstractExchangeActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in AbstractExchangeActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
