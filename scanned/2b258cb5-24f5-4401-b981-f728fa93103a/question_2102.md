# Q2102: UpdateAssetActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAssetActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` — where the attacker replays or batches UpdateAssetActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that UpdateAssetActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` -> `UpdateAssetActuator.execute`
- Entrypoint: broadcast UpdateAssetActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `UpdateAssetActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches UpdateAssetActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: UpdateAssetActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing UpdateAssetActuator twice and asserting single effect
