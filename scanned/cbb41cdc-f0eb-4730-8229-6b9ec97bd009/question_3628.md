# Q3628: ParticipateAssetIssueActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ParticipateAssetIssueActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java` — where the attacker replays or batches ParticipateAssetIssueActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that ParticipateAssetIssueActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java` -> `ParticipateAssetIssueActuator.validate`
- Entrypoint: broadcast ParticipateAssetIssueActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `ParticipateAssetIssueActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches ParticipateAssetIssueActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: ParticipateAssetIssueActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing ParticipateAssetIssueActuator twice and asserting single effect
