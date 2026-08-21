# Q856: FreezeBalanceActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `FreezeBalanceActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java` — where the attacker replays or batches FreezeBalanceActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that FreezeBalanceActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java` -> `FreezeBalanceActuator.validate`
- Entrypoint: broadcast FreezeBalanceActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `FreezeBalanceActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches FreezeBalanceActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: FreezeBalanceActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing FreezeBalanceActuator twice and asserting single effect
