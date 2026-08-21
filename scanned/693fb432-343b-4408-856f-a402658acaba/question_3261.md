# Q3261: WithdrawExpireUnfreezeActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `WithdrawExpireUnfreezeActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java` — where the attacker sizes amounts in WithdrawExpireUnfreezeActuator so a subtraction in WithdrawExpireUnfreezeActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in WithdrawExpireUnfreezeActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java` -> `WithdrawExpireUnfreezeActuator.calcFee`
- Entrypoint: broadcast WithdrawExpireUnfreezeActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `WithdrawExpireUnfreezeActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in WithdrawExpireUnfreezeActuator so a subtraction in WithdrawExpireUnfreezeActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in WithdrawExpireUnfreezeActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
