# Q1727: SetAccountIdActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `SetAccountIdActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java` — where the attacker submits SetAccountIdActuator with a zero amount, self-referential owner==to, or empty target that SetAccountIdActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that SetAccountIdActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java` -> `SetAccountIdActuator.calcFee`
- Entrypoint: broadcast SetAccountIdActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `SetAccountIdActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits SetAccountIdActuator with a zero amount, self-referential owner==to, or empty target that SetAccountIdActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: SetAccountIdActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
