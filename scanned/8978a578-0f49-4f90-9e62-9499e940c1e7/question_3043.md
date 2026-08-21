# Q3043: AssetIssueActuator: zero/self operand edge

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AssetIssueActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java` — where the attacker submits AssetIssueActuator with a zero amount, self-referential owner==to, or empty target that AssetIssueActuator.validate fails to reject, corrupting downstream accounting — to break the invariant that AssetIssueActuator.validate rejects zero, self, and empty operands that break accounting, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java` -> `AssetIssueActuator.calcFee`
- Entrypoint: broadcast AssetIssueActuator with zero/self operand
- Attacker controls: request/transaction/contract inputs to `AssetIssueActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits AssetIssueActuator with a zero amount, self-referential owner==to, or empty target that AssetIssueActuator.validate fails to reject, corrupting downstream accounting
- Invariant to test: AssetIssueActuator.validate rejects zero, self, and empty operands that break accounting
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit with amount=0 and owner==to asserting rejection
