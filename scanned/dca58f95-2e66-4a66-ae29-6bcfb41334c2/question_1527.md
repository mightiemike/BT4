# Q1527: FreezeBalanceActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `FreezeBalanceActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java` — where the attacker submits FreezeBalanceActuator with a zero amount, self-referential owner==to, or empty target that FreezeBalanceActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that FreezeBalanceActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java` -> `FreezeBalanceActuator.calcFee`
- Entrypoint: broadcast FreezeBalanceActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `FreezeBalanceActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits FreezeBalanceActuator with a zero amount, self-referential owner==to, or empty target that FreezeBalanceActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: FreezeBalanceActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
