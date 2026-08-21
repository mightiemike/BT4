# Q1528: UnfreezeAssetActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UnfreezeAssetActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UnfreezeAssetActuator.java` — where the attacker submits UnfreezeAssetActuator with a zero amount, self-referential owner==to, or empty target that UnfreezeAssetActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that UnfreezeAssetActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UnfreezeAssetActuator.java` -> `UnfreezeAssetActuator.execute`
- Entrypoint: broadcast UnfreezeAssetActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `UnfreezeAssetActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits UnfreezeAssetActuator with a zero amount, self-referential owner==to, or empty target that UnfreezeAssetActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: UnfreezeAssetActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
