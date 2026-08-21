# Q3466: TransferActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransferActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java` — where the attacker submits TransferActuator with a zero amount, self-referential owner==to, or empty target that TransferActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that TransferActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java` -> `TransferActuator.calcFee`
- Entrypoint: broadcast TransferActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `TransferActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits TransferActuator with a zero amount, self-referential owner==to, or empty target that TransferActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: TransferActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
