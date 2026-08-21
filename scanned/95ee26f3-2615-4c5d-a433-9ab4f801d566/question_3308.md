# Q3308: TransferAssetActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransferAssetActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java` — where the attacker submits TransferAssetActuator with a zero amount, self-referential owner==to, or empty target that TransferAssetActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that TransferAssetActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java` -> `TransferAssetActuator.execute`
- Entrypoint: broadcast TransferAssetActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `TransferAssetActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits TransferAssetActuator with a zero amount, self-referential owner==to, or empty target that TransferAssetActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: TransferAssetActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
