# Q3760: DelegateResourceActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegateResourceActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java` — where the attacker submits DelegateResourceActuator with a zero amount, self-referential owner==to, or empty target that DelegateResourceActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that DelegateResourceActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java` -> `DelegateResourceActuator.validate`
- Entrypoint: broadcast DelegateResourceActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `DelegateResourceActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits DelegateResourceActuator with a zero amount, self-referential owner==to, or empty target that DelegateResourceActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: DelegateResourceActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
