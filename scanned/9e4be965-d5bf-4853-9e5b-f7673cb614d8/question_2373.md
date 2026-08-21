# Q2373: ActuatorCreator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ActuatorCreator.init` in `actuator/src/main/java/org/tron/core/actuator/ActuatorCreator.java` — where the attacker submits ActuatorCreator with a zero amount, self-referential owner==to, or empty target that ActuatorCreator.validate fails to reject, corrupting downstream accounting — to break the invariant that ActuatorCreator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ActuatorCreator.java` -> `ActuatorCreator.init`
- Entrypoint: broadcast ActuatorCreator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `ActuatorCreator.init` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits ActuatorCreator with a zero amount, self-referential owner==to, or empty target that ActuatorCreator.validate fails to reject, corrupting downstream accounting
- Invariant to test: ActuatorCreator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
