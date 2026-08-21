# Q1446: AbstractExchangeActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AbstractExchangeActuator.allowHarden` in `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` — where the attacker submits AbstractExchangeActuator with a zero amount, self-referential owner==to, or empty target that AbstractExchangeActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that AbstractExchangeActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` -> `AbstractExchangeActuator.allowHarden`
- Entrypoint: broadcast AbstractExchangeActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `AbstractExchangeActuator.allowHarden` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits AbstractExchangeActuator with a zero amount, self-referential owner==to, or empty target that AbstractExchangeActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: AbstractExchangeActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
