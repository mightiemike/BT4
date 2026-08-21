# Q3476: MarketSellAssetActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketSellAssetActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java` — where the attacker sizes amounts in MarketSellAssetActuator so a subtraction in MarketSellAssetActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in MarketSellAssetActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java` -> `MarketSellAssetActuator.calcFee`
- Entrypoint: broadcast MarketSellAssetActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `MarketSellAssetActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in MarketSellAssetActuator so a subtraction in MarketSellAssetActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in MarketSellAssetActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
