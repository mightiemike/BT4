# Q3009: TransferAssetActuator: expiration/time boundary

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransferAssetActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java` — where the attacker exploits an off-by-one in TransferAssetActuator's time/expire/maintenance comparison to unfreeze or withdraw early — to break the invariant that time-gated state in TransferAssetActuator changes only at or after the true boundary, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java` -> `TransferAssetActuator.execute`
- Entrypoint: broadcast TransferAssetActuator at the expire boundary
- Attacker controls: request/transaction/contract inputs to `TransferAssetActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits an off-by-one in TransferAssetActuator's time/expire/maintenance comparison to unfreeze or withdraw early
- Invariant to test: time-gated state in TransferAssetActuator changes only at or after the true boundary
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at expireTime-1/expireTime asserting correct gating
