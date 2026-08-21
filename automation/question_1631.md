# Q1631: UpdateSettingContractActuator: resource-before-balance ordering

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateSettingContractActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java` — where the attacker orders operands in UpdateSettingContractActuator so UpdateSettingContractActuator.execute mutates resource state before a balance check fails, leaving partial state — to break the invariant that UpdateSettingContractActuator.execute is atomic: no partial mutation on a failed precondition, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java` -> `UpdateSettingContractActuator.calcFee`
- Entrypoint: broadcast UpdateSettingContractActuator that fails mid-execute
- Attacker controls: request/transaction/contract inputs to `UpdateSettingContractActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: orders operands in UpdateSettingContractActuator so UpdateSettingContractActuator.execute mutates resource state before a balance check fails, leaving partial state
- Invariant to test: UpdateSettingContractActuator.execute is atomic: no partial mutation on a failed precondition
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit forcing mid-execute failure asserting rollback
