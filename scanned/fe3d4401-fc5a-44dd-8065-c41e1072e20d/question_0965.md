# Q965: AccountPermissionUpdateActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountPermissionUpdateActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java` — where the attacker replays or batches AccountPermissionUpdateActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that AccountPermissionUpdateActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java` -> `AccountPermissionUpdateActuator.execute`
- Entrypoint: broadcast AccountPermissionUpdateActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `AccountPermissionUpdateActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches AccountPermissionUpdateActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: AccountPermissionUpdateActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing AccountPermissionUpdateActuator twice and asserting single effect
