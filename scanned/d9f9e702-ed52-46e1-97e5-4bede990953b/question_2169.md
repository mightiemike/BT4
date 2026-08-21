# Q2169: UnDelegateResourceActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UnDelegateResourceActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java` — where the attacker submits UnDelegateResourceActuator with a zero amount, self-referential owner==to, or empty target that UnDelegateResourceActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that UnDelegateResourceActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java` -> `UnDelegateResourceActuator.validate`
- Entrypoint: broadcast UnDelegateResourceActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `UnDelegateResourceActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits UnDelegateResourceActuator with a zero amount, self-referential owner==to, or empty target that UnDelegateResourceActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: UnDelegateResourceActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
