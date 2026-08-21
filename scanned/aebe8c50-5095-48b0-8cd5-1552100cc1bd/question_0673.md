# Q673: TransferAssetActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransferAssetActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java` — where the attacker sizes amounts in TransferAssetActuator so a subtraction in TransferAssetActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in TransferAssetActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java` -> `TransferAssetActuator.calcFee`
- Entrypoint: broadcast TransferAssetActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `TransferAssetActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in TransferAssetActuator so a subtraction in TransferAssetActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in TransferAssetActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
