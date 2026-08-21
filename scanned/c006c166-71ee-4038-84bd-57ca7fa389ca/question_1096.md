# Q1096: UpdateAccountActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAccountActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java` — where the attacker replays or batches UpdateAccountActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that UpdateAccountActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java` -> `UpdateAccountActuator.execute`
- Entrypoint: broadcast UpdateAccountActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `UpdateAccountActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches UpdateAccountActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: UpdateAccountActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing UpdateAccountActuator twice and asserting single effect
