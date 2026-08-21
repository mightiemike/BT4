# Q3463: AbstractActuator: expiration/time boundary

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AbstractActuator.floorDiv` in `actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java` — where the attacker exploits an off-by-one in AbstractActuator's time/expire/maintenance comparison to unfreeze or withdraw early — to break the invariant that time-gated state in AbstractActuator changes only at or after the true boundary, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java` -> `AbstractActuator.floorDiv`
- Entrypoint: broadcast AbstractActuator at the expire boundary
- Attacker controls: request/transaction/contract inputs to `AbstractActuator.floorDiv` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits an off-by-one in AbstractActuator's time/expire/maintenance comparison to unfreeze or withdraw early
- Invariant to test: time-gated state in AbstractActuator changes only at or after the true boundary
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at expireTime-1/expireTime asserting correct gating
