# Q2330: ExchangeWithdrawActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeWithdrawActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` — where the attacker submits ExchangeWithdrawActuator with a zero amount, self-referential owner==to, or empty target that ExchangeWithdrawActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that ExchangeWithdrawActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` -> `ExchangeWithdrawActuator.execute`
- Entrypoint: broadcast ExchangeWithdrawActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `ExchangeWithdrawActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits ExchangeWithdrawActuator with a zero amount, self-referential owner==to, or empty target that ExchangeWithdrawActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: ExchangeWithdrawActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
