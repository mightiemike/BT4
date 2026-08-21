# Q2954: FreezeBalanceActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `FreezeBalanceActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java` — where the attacker sizes amounts in FreezeBalanceActuator so a subtraction in FreezeBalanceActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in FreezeBalanceActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java` -> `FreezeBalanceActuator.calcFee`
- Entrypoint: broadcast FreezeBalanceActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `FreezeBalanceActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in FreezeBalanceActuator so a subtraction in FreezeBalanceActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in FreezeBalanceActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
