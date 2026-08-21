# Q1988: AbstractActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AbstractActuator.addExact` in `actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java` — where the attacker sizes amounts in AbstractActuator so a subtraction in AbstractActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in AbstractActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java` -> `AbstractActuator.addExact`
- Entrypoint: broadcast AbstractActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `AbstractActuator.addExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in AbstractActuator so a subtraction in AbstractActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in AbstractActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
