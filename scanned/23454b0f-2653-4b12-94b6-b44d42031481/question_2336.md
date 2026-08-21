# Q2336: TransferActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransferActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java` — where the attacker sizes amounts in TransferActuator so a subtraction in TransferActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in TransferActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java` -> `TransferActuator.validate`
- Entrypoint: broadcast TransferActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `TransferActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in TransferActuator so a subtraction in TransferActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in TransferActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
