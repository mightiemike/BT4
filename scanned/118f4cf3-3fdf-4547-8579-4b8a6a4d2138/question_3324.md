# Q3324: AssetIssueActuator: balance underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AssetIssueActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java` — where the attacker sizes amounts in AssetIssueActuator so a subtraction in AssetIssueActuator.execute underflows or a sum overflows past the conservation check — to break the invariant that balances and supply never underflow or exceed issuance in AssetIssueActuator, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java` -> `AssetIssueActuator.execute`
- Entrypoint: broadcast AssetIssueActuator with boundary amounts
- Attacker controls: request/transaction/contract inputs to `AssetIssueActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sizes amounts in AssetIssueActuator so a subtraction in AssetIssueActuator.execute underflows or a sum overflows past the conservation check
- Invariant to test: balances and supply never underflow or exceed issuance in AssetIssueActuator
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit execute() with MAX/near-zero balances asserting conservation
