# Q321: UnfreezeBalanceV2Actuator: expiration/time boundary

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UnfreezeBalanceV2Actuator.validate` in `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java` — where the attacker exploits an off-by-one in UnfreezeBalanceV2Actuator's time/expire/maintenance comparison to unfreeze or withdraw early — to break the invariant that time-gated state in UnfreezeBalanceV2Actuator changes only at or after the true boundary, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java` -> `UnfreezeBalanceV2Actuator.validate`
- Entrypoint: broadcast UnfreezeBalanceV2Actuator at the expire boundary
- Attacker controls: request/transaction/contract inputs to `UnfreezeBalanceV2Actuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits an off-by-one in UnfreezeBalanceV2Actuator's time/expire/maintenance comparison to unfreeze or withdraw early
- Invariant to test: time-gated state in UnfreezeBalanceV2Actuator changes only at or after the true boundary
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at expireTime-1/expireTime asserting correct gating
