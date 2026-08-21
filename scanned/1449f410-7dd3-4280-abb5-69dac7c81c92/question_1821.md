# Q1821: UnfreezeAssetActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UnfreezeAssetActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UnfreezeAssetActuator.java` — where the attacker replays or batches UnfreezeAssetActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that UnfreezeAssetActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UnfreezeAssetActuator.java` -> `UnfreezeAssetActuator.execute`
- Entrypoint: broadcast UnfreezeAssetActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `UnfreezeAssetActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches UnfreezeAssetActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: UnfreezeAssetActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing UnfreezeAssetActuator twice and asserting single effect
