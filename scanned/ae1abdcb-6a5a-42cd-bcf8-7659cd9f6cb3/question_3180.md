# Q3180: SetAccountIdActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `SetAccountIdActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java` — where the attacker sizes amounts in SetAccountIdActuator so a subtraction in SetAccountIdActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in SetAccountIdActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java` -> `SetAccountIdActuator.calcFee`
- Entrypoint: broadcast SetAccountIdActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `SetAccountIdActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in SetAccountIdActuator so a subtraction in SetAccountIdActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in SetAccountIdActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
