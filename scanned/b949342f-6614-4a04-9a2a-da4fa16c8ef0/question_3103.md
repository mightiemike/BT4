# Q3103: MarketCancelOrderActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketCancelOrderActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java` — where the attacker sizes amounts in MarketCancelOrderActuator so a subtraction in MarketCancelOrderActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in MarketCancelOrderActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/MarketCancelOrderActuator.java` -> `MarketCancelOrderActuator.calcFee`
- Entrypoint: broadcast MarketCancelOrderActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `MarketCancelOrderActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in MarketCancelOrderActuator so a subtraction in MarketCancelOrderActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in MarketCancelOrderActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
