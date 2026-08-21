# Q3397: WithdrawBalanceActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `WithdrawBalanceActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java` — where the attacker sizes amounts in WithdrawBalanceActuator so a subtraction in WithdrawBalanceActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in WithdrawBalanceActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java` -> `WithdrawBalanceActuator.validate`
- Entrypoint: broadcast WithdrawBalanceActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `WithdrawBalanceActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in WithdrawBalanceActuator so a subtraction in WithdrawBalanceActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in WithdrawBalanceActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
