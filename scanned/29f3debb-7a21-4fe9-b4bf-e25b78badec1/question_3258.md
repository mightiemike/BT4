# Q3258: ExchangeCreateActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeCreateActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` — where the attacker sizes amounts in ExchangeCreateActuator so a subtraction in ExchangeCreateActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in ExchangeCreateActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` -> `ExchangeCreateActuator.execute`
- Entrypoint: broadcast ExchangeCreateActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `ExchangeCreateActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in ExchangeCreateActuator so a subtraction in ExchangeCreateActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in ExchangeCreateActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
