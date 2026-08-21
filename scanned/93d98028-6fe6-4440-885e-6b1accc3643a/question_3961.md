# Q3961: AbstractActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AbstractActuator.floorDiv` in `actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java` — where the attacker submits AbstractActuator with a zero amount, self-referential owner==to, or empty target that AbstractActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that AbstractActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java` -> `AbstractActuator.floorDiv`
- Entrypoint: broadcast AbstractActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `AbstractActuator.floorDiv` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits AbstractActuator with a zero amount, self-referential owner==to, or empty target that AbstractActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: AbstractActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
