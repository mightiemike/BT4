# Q1568: UnDelegateResourceActuator: expiration/time boundary

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UnDelegateResourceActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java` — where the attacker exploits an off-by-one in UnDelegateResourceActuator's time/expire/maintenance comparison to unfreeze or withdraw early — to break the invariant that time-gated state in UnDelegateResourceActuator changes only at or after the true boundary, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java` -> `UnDelegateResourceActuator.execute`
- Entrypoint: broadcast UnDelegateResourceActuator at the expire boundary
- Attacker controls: request/transaction/contract inputs to `UnDelegateResourceActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits an off-by-one in UnDelegateResourceActuator's time/expire/maintenance comparison to unfreeze or withdraw early
- Invariant to test: time-gated state in UnDelegateResourceActuator changes only at or after the true boundary
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at expireTime-1/expireTime asserting correct gating
