# Q804: ParticipateAssetIssueActuator: expiration/time boundary

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ParticipateAssetIssueActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java` — where the attacker exploits an off-by-one in ParticipateAssetIssueActuator's time/expire/maintenance comparison to unfreeze or withdraw early — to break the invariant that time-gated state in ParticipateAssetIssueActuator changes only at or after the true boundary, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java` -> `ParticipateAssetIssueActuator.execute`
- Entrypoint: broadcast ParticipateAssetIssueActuator at the expire boundary
- Attacker controls: request/transaction/contract inputs to `ParticipateAssetIssueActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits an off-by-one in ParticipateAssetIssueActuator's time/expire/maintenance comparison to unfreeze or withdraw early
- Invariant to test: time-gated state in ParticipateAssetIssueActuator changes only at or after the true boundary
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at expireTime-1/expireTime asserting correct gating
