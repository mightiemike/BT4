# Q2035: ExchangeInjectActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeInjectActuator.doValidate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` — where the attacker submits ExchangeInjectActuator with a zero amount, self-referential owner==to, or empty target that ExchangeInjectActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that ExchangeInjectActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` -> `ExchangeInjectActuator.doValidate`
- Entrypoint: broadcast ExchangeInjectActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `ExchangeInjectActuator.doValidate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits ExchangeInjectActuator with a zero amount, self-referential owner==to, or empty target that ExchangeInjectActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: ExchangeInjectActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
