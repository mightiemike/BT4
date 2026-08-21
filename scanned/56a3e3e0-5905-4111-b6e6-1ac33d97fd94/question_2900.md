# Q2900: ParticipateAssetIssueActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ParticipateAssetIssueActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java` — where the attacker sizes amounts in ParticipateAssetIssueActuator so a subtraction in ParticipateAssetIssueActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in ParticipateAssetIssueActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java` -> `ParticipateAssetIssueActuator.validate`
- Entrypoint: broadcast ParticipateAssetIssueActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `ParticipateAssetIssueActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in ParticipateAssetIssueActuator so a subtraction in ParticipateAssetIssueActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in ParticipateAssetIssueActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
