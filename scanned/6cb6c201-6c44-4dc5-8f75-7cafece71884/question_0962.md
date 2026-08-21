# Q962: TransferAssetActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransferAssetActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java` — where the attacker replays or batches TransferAssetActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that TransferAssetActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java` -> `TransferAssetActuator.validate`
- Entrypoint: broadcast TransferAssetActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `TransferAssetActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches TransferAssetActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: TransferAssetActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing TransferAssetActuator twice and asserting single effect
