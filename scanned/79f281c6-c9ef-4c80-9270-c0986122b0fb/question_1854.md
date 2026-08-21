# Q1854: ExchangeCreateActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeCreateActuator.doValidate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` — where the attacker submits ExchangeCreateActuator with a zero amount, self-referential owner==to, or empty target that ExchangeCreateActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that ExchangeCreateActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` -> `ExchangeCreateActuator.doValidate`
- Entrypoint: broadcast ExchangeCreateActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `ExchangeCreateActuator.doValidate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits ExchangeCreateActuator with a zero amount, self-referential owner==to, or empty target that ExchangeCreateActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: ExchangeCreateActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
