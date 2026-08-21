# Q2103: UpdateSettingContractActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateSettingContractActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java` — where the attacker replays or batches UpdateSettingContractActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that UpdateSettingContractActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java` -> `UpdateSettingContractActuator.execute`
- Entrypoint: broadcast UpdateSettingContractActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `UpdateSettingContractActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches UpdateSettingContractActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: UpdateSettingContractActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing UpdateSettingContractActuator twice and asserting single effect
