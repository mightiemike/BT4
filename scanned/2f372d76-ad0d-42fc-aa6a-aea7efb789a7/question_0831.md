# Q831: UpdateEnergyLimitContractActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateEnergyLimitContractActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java` — where the attacker sizes amounts in UpdateEnergyLimitContractActuator so a subtraction in UpdateEnergyLimitContractActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in UpdateEnergyLimitContractActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java` -> `UpdateEnergyLimitContractActuator.calcFee`
- Entrypoint: broadcast UpdateEnergyLimitContractActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `UpdateEnergyLimitContractActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in UpdateEnergyLimitContractActuator so a subtraction in UpdateEnergyLimitContractActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in UpdateEnergyLimitContractActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
