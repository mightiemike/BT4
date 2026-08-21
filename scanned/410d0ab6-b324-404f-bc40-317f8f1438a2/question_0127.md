# Q127: AssetIssueActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AssetIssueActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java` — where the attacker replays or batches AssetIssueActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that AssetIssueActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AssetIssueActuator.java` -> `AssetIssueActuator.validate`
- Entrypoint: broadcast AssetIssueActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `AssetIssueActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches AssetIssueActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: AssetIssueActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing AssetIssueActuator twice and asserting single effect
