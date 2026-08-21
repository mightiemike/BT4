# Q2174: ShieldedTransferActuator: expiration/time boundary

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ShieldedTransferActuator.calcFee` in `actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java` — where the attacker exploits an off-by-one in ShieldedTransferActuator's time/expire/maintenance comparison to unfreeze or withdraw early — to break the invariant that time-gated state in ShieldedTransferActuator changes only at or after the true boundary, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java` -> `ShieldedTransferActuator.calcFee`
- Entrypoint: broadcast ShieldedTransferActuator at the expire boundary
- Attacker controls: request/transaction/contract inputs to `ShieldedTransferActuator.calcFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits an off-by-one in ShieldedTransferActuator's time/expire/maintenance comparison to unfreeze or withdraw early
- Invariant to test: time-gated state in ShieldedTransferActuator changes only at or after the true boundary
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at expireTime-1/expireTime asserting correct gating
