# Q215: WithdrawBalanceActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `WithdrawBalanceActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java` — where the attacker submits WithdrawBalanceActuator with a zero amount, self-referential owner==to, or empty target that WithdrawBalanceActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that WithdrawBalanceActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java` -> `WithdrawBalanceActuator.validate`
- Entrypoint: broadcast WithdrawBalanceActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `WithdrawBalanceActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits WithdrawBalanceActuator with a zero amount, self-referential owner==to, or empty target that WithdrawBalanceActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: WithdrawBalanceActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
