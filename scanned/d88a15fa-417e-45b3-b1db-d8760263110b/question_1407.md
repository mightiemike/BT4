# Q1407: TransferActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransferActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java` — where the attacker replays or batches TransferActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that TransferActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/TransferActuator.java` -> `TransferActuator.execute`
- Entrypoint: broadcast TransferActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `TransferActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches TransferActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: TransferActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing TransferActuator twice and asserting single effect
