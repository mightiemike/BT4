# Q2912: UpdateAssetActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAssetActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` — where the attacker submits UpdateAssetActuator with a zero amount, self-referential owner==to, or empty target that UpdateAssetActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that UpdateAssetActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` -> `UpdateAssetActuator.calcFee`
- Entrypoint: broadcast UpdateAssetActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `UpdateAssetActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits UpdateAssetActuator with a zero amount, self-referential owner==to, or empty target that UpdateAssetActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: UpdateAssetActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
