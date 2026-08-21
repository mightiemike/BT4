# Q2656: VMActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VMActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/VMActuator.java` — where the attacker replays or batches VMActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that VMActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/VMActuator.java` -> `VMActuator.execute`
- Entrypoint: broadcast VMActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `VMActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches VMActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: VMActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing VMActuator twice and asserting single effect
