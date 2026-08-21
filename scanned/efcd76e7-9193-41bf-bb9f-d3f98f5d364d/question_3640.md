# Q3640: UpdateAssetActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAssetActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` — where the attacker sizes amounts in UpdateAssetActuator so a subtraction in UpdateAssetActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in UpdateAssetActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` -> `UpdateAssetActuator.execute`
- Entrypoint: broadcast UpdateAssetActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `UpdateAssetActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in UpdateAssetActuator so a subtraction in UpdateAssetActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in UpdateAssetActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
