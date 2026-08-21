# Q2999: AccountPermissionUpdateActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountPermissionUpdateActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java` — where the attacker submits AccountPermissionUpdateActuator with a zero amount, self-referential owner==to, or empty target that AccountPermissionUpdateActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that AccountPermissionUpdateActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java` -> `AccountPermissionUpdateActuator.calcFee`
- Entrypoint: broadcast AccountPermissionUpdateActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `AccountPermissionUpdateActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits AccountPermissionUpdateActuator with a zero amount, self-referential owner==to, or empty target that AccountPermissionUpdateActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: AccountPermissionUpdateActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
