# Q2731: ExchangeTransactionActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeTransactionActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java` — where the attacker submits ExchangeTransactionActuator with a zero amount, self-referential owner==to, or empty target that ExchangeTransactionActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that ExchangeTransactionActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java` -> `ExchangeTransactionActuator.execute`
- Entrypoint: broadcast ExchangeTransactionActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `ExchangeTransactionActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits ExchangeTransactionActuator with a zero amount, self-referential owner==to, or empty target that ExchangeTransactionActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: ExchangeTransactionActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
