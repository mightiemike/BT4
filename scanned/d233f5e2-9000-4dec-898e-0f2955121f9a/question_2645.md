# Q2645: UnDelegateResourceActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UnDelegateResourceActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java` — where the attacker sizes amounts in UnDelegateResourceActuator so a subtraction in UnDelegateResourceActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in UnDelegateResourceActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java` -> `UnDelegateResourceActuator.validate`
- Entrypoint: broadcast UnDelegateResourceActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `UnDelegateResourceActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in UnDelegateResourceActuator so a subtraction in UnDelegateResourceActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in UnDelegateResourceActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
