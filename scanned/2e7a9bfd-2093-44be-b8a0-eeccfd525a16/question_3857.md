# Q3857: UpdateSettingContractActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateSettingContractActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java` — where the attacker sizes amounts in UpdateSettingContractActuator so a subtraction in UpdateSettingContractActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in UpdateSettingContractActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java` -> `UpdateSettingContractActuator.calcFee`
- Entrypoint: broadcast UpdateSettingContractActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `UpdateSettingContractActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in UpdateSettingContractActuator so a subtraction in UpdateSettingContractActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in UpdateSettingContractActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
