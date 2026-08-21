# Q1947: SetAccountIdActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `SetAccountIdActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java` — where the attacker replays or batches SetAccountIdActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that SetAccountIdActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java` -> `SetAccountIdActuator.execute`
- Entrypoint: broadcast SetAccountIdActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `SetAccountIdActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches SetAccountIdActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: SetAccountIdActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing SetAccountIdActuator twice and asserting single effect
