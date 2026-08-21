# Q796: ActuatorCreator: expiration/time boundary

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ActuatorCreator.init` in `actuator/src/main/java/org/tron/core/actuator/ActuatorCreator.java` — where the attacker exploits an off-by-one in ActuatorCreator's time/expire/maintenance comparison to unfreeze or withdraw early — to break the invariant that time-gated state in ActuatorCreator changes only at or after the true boundary, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ActuatorCreator.java` -> `ActuatorCreator.init`
- Entrypoint: broadcast ActuatorCreator at the expire boundary
- Attacker controls: request/transaction/contract inputs to `ActuatorCreator.init` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits an off-by-one in ActuatorCreator's time/expire/maintenance comparison to unfreeze or withdraw early
- Invariant to test: time-gated state in ActuatorCreator changes only at or after the true boundary
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at expireTime-1/expireTime asserting correct gating
