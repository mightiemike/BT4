# Q2932: ShieldedTransferActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ShieldedTransferActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java` — where the attacker sizes amounts in ShieldedTransferActuator so a subtraction in ShieldedTransferActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in ShieldedTransferActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java` -> `ShieldedTransferActuator.execute`
- Entrypoint: broadcast ShieldedTransferActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `ShieldedTransferActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in ShieldedTransferActuator so a subtraction in ShieldedTransferActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in ShieldedTransferActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
