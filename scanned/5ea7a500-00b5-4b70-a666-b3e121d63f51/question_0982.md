# Q982: ExchangeWithdrawActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeWithdrawActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` — where the attacker sizes amounts in ExchangeWithdrawActuator so a subtraction in ExchangeWithdrawActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in ExchangeWithdrawActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` -> `ExchangeWithdrawActuator.calcFee`
- Entrypoint: broadcast ExchangeWithdrawActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `ExchangeWithdrawActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in ExchangeWithdrawActuator so a subtraction in ExchangeWithdrawActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in ExchangeWithdrawActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
