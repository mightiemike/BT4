# Q2402: ExchangeTransactionActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeTransactionActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java` — where the attacker sizes amounts in ExchangeTransactionActuator so a subtraction in ExchangeTransactionActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in ExchangeTransactionActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java` -> `ExchangeTransactionActuator.validate`
- Entrypoint: broadcast ExchangeTransactionActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `ExchangeTransactionActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in ExchangeTransactionActuator so a subtraction in ExchangeTransactionActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in ExchangeTransactionActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
