# Q785: UpdateSettingContractActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateSettingContractActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java` — where the attacker submits UpdateSettingContractActuator with a zero amount, self-referential owner==to, or empty target that UpdateSettingContractActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that UpdateSettingContractActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java` -> `UpdateSettingContractActuator.calcFee`
- Entrypoint: broadcast UpdateSettingContractActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `UpdateSettingContractActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits UpdateSettingContractActuator with a zero amount, self-referential owner==to, or empty target that UpdateSettingContractActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: UpdateSettingContractActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
