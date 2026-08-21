# Q925: WithdrawExpireUnfreezeActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `WithdrawExpireUnfreezeActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java` — where the attacker submits WithdrawExpireUnfreezeActuator with a zero amount, self-referential owner==to, or empty target that WithdrawExpireUnfreezeActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that WithdrawExpireUnfreezeActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java` -> `WithdrawExpireUnfreezeActuator.calcFee`
- Entrypoint: broadcast WithdrawExpireUnfreezeActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `WithdrawExpireUnfreezeActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits WithdrawExpireUnfreezeActuator with a zero amount, self-referential owner==to, or empty target that WithdrawExpireUnfreezeActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: WithdrawExpireUnfreezeActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
