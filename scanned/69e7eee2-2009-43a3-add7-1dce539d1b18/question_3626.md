# Q3626: ExchangeInjectActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeInjectActuator.doValidate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` — where the attacker sizes amounts in ExchangeInjectActuator so a subtraction in ExchangeInjectActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in ExchangeInjectActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` -> `ExchangeInjectActuator.doValidate`
- Entrypoint: broadcast ExchangeInjectActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `ExchangeInjectActuator.doValidate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in ExchangeInjectActuator so a subtraction in ExchangeInjectActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in ExchangeInjectActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
