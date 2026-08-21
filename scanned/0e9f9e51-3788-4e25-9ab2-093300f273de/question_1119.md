# Q1119: VMActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VMActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/VMActuator.java` — where the attacker submits VMActuator with a zero amount, self-referential owner==to, or empty target that VMActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that VMActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/VMActuator.java` -> `VMActuator.validate`
- Entrypoint: broadcast VMActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `VMActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits VMActuator with a zero amount, self-referential owner==to, or empty target that VMActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: VMActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
