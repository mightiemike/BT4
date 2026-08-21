# Q1199: UnfreezeBalanceActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UnfreezeBalanceActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java` — where the attacker sizes amounts in UnfreezeBalanceActuator so a subtraction in UnfreezeBalanceActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in UnfreezeBalanceActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java` -> `UnfreezeBalanceActuator.execute`
- Entrypoint: broadcast UnfreezeBalanceActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `UnfreezeBalanceActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in UnfreezeBalanceActuator so a subtraction in UnfreezeBalanceActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in UnfreezeBalanceActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
