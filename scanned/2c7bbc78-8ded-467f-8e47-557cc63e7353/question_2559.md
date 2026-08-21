# Q2559: MarketCancelOrderActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketCancelOrderActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java` — where the attacker submits MarketCancelOrderActuator with a zero amount, self-referential owner==to, or empty target that MarketCancelOrderActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that MarketCancelOrderActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java` -> `MarketCancelOrderActuator.calcFee`
- Entrypoint: broadcast MarketCancelOrderActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `MarketCancelOrderActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits MarketCancelOrderActuator with a zero amount, self-referential owner==to, or empty target that MarketCancelOrderActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: MarketCancelOrderActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
